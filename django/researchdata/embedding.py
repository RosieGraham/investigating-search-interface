"""
Vector classification for the Investigating Search Interface.

Replaces substring trigger matching with semantic matching: the user's query
and every Topic's description are embedded as 384-dimensional sentence vectors
(SBERT family model, ONNX runtime), and the query is assigned to the
nearest topic by cosine similarity, provided it clears a confidence threshold.

Academic basis: WC-SBERT (Chi & Jang 2024, ACM TIST, DOI 10.1145/3678183) -
zero-shot topic classification via sentence embeddings. Model:
sentence-transformers/multi-qa-MiniLM-L6-cos-v1 (384-dim, tuned for
question/search-style text).

Design notes:
- No PyTorch. The model runs through onnxruntime (int8-quantised where
  available), which keeps the whole process inside a 512MB container.
- The topic index (193 x 384 float32 matrix) is built from the database,
  cached in memory, and persisted to disk so restarts don't re-embed.
- Saving/deleting a Topic in the admin marks the index dirty; it rebuilds
  lazily on the next query.
- Every public function degrades gracefully: if the model files are missing
  or onnxruntime is unavailable, callers get ClassifierUnavailable and fall
  back to trigger matching.
- The classifier is intentionally behind a narrow interface (classify_query)
  so an alternative backend (e.g. a PeARS API, per Aurelie Herbelot's offer)
  could replace it without touching the views.
"""

import hashlib
import json
import logging
import threading

import numpy as np
from django.conf import settings

logger = logging.getLogger('researchdata')

# Filenames inside settings.EMBEDDING_MODEL_DIR
MODEL_FILENAME = 'model.onnx'
TOKENIZER_FILENAME = 'tokenizer.json'
INDEX_FILENAME = 'topic_index.npz'
INDEX_META_FILENAME = 'topic_index_meta.json'

MAX_TOKENS = 256

_lock = threading.Lock()
_session = None          # onnxruntime.InferenceSession
_tokenizer = None        # tokenizers.Tokenizer
_input_names = None      # model input names, detected from the session
_index_matrix = None     # np.ndarray (n_topics, 384), L2-normalised
_index_topic_ids = None  # list[int], row-aligned with _index_matrix
_index_dirty = True
_unavailable_logged = False


class ClassifierUnavailable(Exception):
    """Raised when the model or runtime cannot be loaded. Callers fall back."""


def _model_dir():
    d = settings.EMBEDDING_MODEL_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_runtime():
    """Load tokenizer + ONNX session once per process."""
    global _session, _tokenizer, _input_names, _unavailable_logged
    if _session is not None and _tokenizer is not None:
        return
    model_path = _model_dir() / MODEL_FILENAME
    tokenizer_path = _model_dir() / TOKENIZER_FILENAME
    if not model_path.exists() or not tokenizer_path.exists():
        if not _unavailable_logged:
            logger.warning(
                'Classifier model files missing in %s - run "manage.py download_model". '
                'Falling back to trigger matching.', _model_dir()
            )
            _unavailable_logged = True
        raise ClassifierUnavailable('model files missing')
    try:
        import onnxruntime as ort
        from tokenizers import Tokenizer
    except ImportError as e:
        if not _unavailable_logged:
            logger.warning('Classifier runtime unavailable (%s). Falling back to trigger matching.', e)
            _unavailable_logged = True
        raise ClassifierUnavailable(str(e))

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_truncation(max_length=MAX_TOKENS)
    tok.enable_padding()  # pad to longest in batch

    so = ort.SessionOptions()
    so.intra_op_num_threads = 1  # behave on 0.1 vCPU containers
    so.inter_op_num_threads = 1
    session = ort.InferenceSession(str(model_path), sess_options=so, providers=['CPUExecutionProvider'])

    _tokenizer = tok
    _session = session
    _input_names = [i.name for i in session.get_inputs()]
    logger.info('Classifier loaded: %s (inputs: %s)', model_path.name, _input_names)


def encode(texts):
    """
    Embed a list of strings -> np.ndarray (n, 384), L2-normalised float32.
    Mean pooling over token embeddings, masked by attention.
    """
    _load_runtime()
    encodings = _tokenizer.encode_batch([t if t else ' ' for t in texts])
    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    feeds = {'input_ids': input_ids, 'attention_mask': attention_mask}
    if 'token_type_ids' in _input_names:
        feeds['token_type_ids'] = np.zeros_like(input_ids)
    # First output: last_hidden_state (n, seq, 384)
    last_hidden = _session.run(None, feeds)[0]
    mask = attention_mask[:, :, None].astype(np.float32)
    summed = (last_hidden * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)
    pooled = summed / counts
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return (pooled / norms).astype(np.float32)


def _topics_fingerprint(rows, model_id):
    payload = json.dumps([(r[0], r[1]) for r in rows], ensure_ascii=False) + '|' + model_id
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def mark_index_dirty(*args, **kwargs):
    """Signal receiver: a Topic changed, rebuild the index on next use."""
    global _index_dirty
    _index_dirty = True


def build_topic_index(force=False):
    """
    Embed all topics and cache the matrix in memory + on disk.
    Returns (matrix, topic_ids). Raises ClassifierUnavailable if no runtime.
    """
    global _index_matrix, _index_topic_ids, _index_dirty
    from .models import Topic

    rows = [(t.id, t.embedding_text) for t in Topic.objects.select_related('topic_group').all()]
    if not rows:
        _index_matrix, _index_topic_ids, _index_dirty = None, [], False
        return None, []

    fingerprint = _topics_fingerprint(rows, settings.EMBEDDING_MODEL_ID)
    index_path = _model_dir() / INDEX_FILENAME
    meta_path = _model_dir() / INDEX_META_FILENAME

    # Reuse the on-disk index when nothing has changed
    if not force and index_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get('fingerprint') == fingerprint:
                data = np.load(index_path)
                _index_matrix = data['matrix']
                _index_topic_ids = data['topic_ids'].tolist()
                _index_dirty = False
                logger.info('Topic index loaded from disk (%d topics).', len(_index_topic_ids))
                return _index_matrix, _index_topic_ids
        except Exception as e:  # corrupt cache: rebuild
            logger.warning('Could not reuse topic index cache (%s); rebuilding.', e)

    logger.info('Building topic index: embedding %d topics...', len(rows))
    matrix = encode([r[1] for r in rows])
    topic_ids = [r[0] for r in rows]
    try:
        np.savez(index_path, matrix=matrix, topic_ids=np.array(topic_ids, dtype=np.int64))
        meta_path.write_text(json.dumps({'fingerprint': fingerprint, 'count': len(topic_ids)}))
    except OSError as e:
        logger.warning('Topic index not persisted (%s); in-memory only.', e)
    _index_matrix, _index_topic_ids, _index_dirty = matrix, topic_ids, False
    logger.info('Topic index built (%d topics).', len(topic_ids))
    return matrix, topic_ids


def _ensure_index():

    if _index_dirty or _index_matrix is None:
        with _lock:
            if _index_dirty or _index_matrix is None:
                build_topic_index()
    return _index_matrix, _index_topic_ids


def classify_query(query_text, threshold=None, top_k=None):
    """
    Return up to top_k (topic_id, confidence) pairs for a query, best first,
    all with confidence >= threshold. Empty list = no confident match.
    Raises ClassifierUnavailable when the model cannot run; callers are
    expected to catch it and use the trigger fallback.
    """
    if threshold is None:
        threshold = settings.CLASSIFIER_THRESHOLD
    if top_k is None:
        top_k = settings.CLASSIFIER_TOP_K

    query_text = (query_text or '').strip()
    if not query_text:
        return []

    matrix, topic_ids = _ensure_index()
    if matrix is None or not len(topic_ids):
        return []

    q = encode([query_text])[0]          # (384,), normalised
    scores = matrix @ q                  # cosine similarity via dot product
    order = np.argsort(-scores)[:max(top_k, 1)]
    return [
        (topic_ids[int(i)], float(scores[int(i)]))
        for i in order
        if float(scores[int(i)]) >= threshold
    ]


def classifier_status():
    """Lightweight status dict for health checks and the admin."""
    model_present = (_model_dir() / MODEL_FILENAME).exists()
    return {
        'enabled': settings.CLASSIFIER_ENABLED,
        'model_id': settings.EMBEDDING_MODEL_ID,
        'model_present': model_present,
        'loaded': _session is not None,
        'index_topics': len(_index_topic_ids) if _index_topic_ids else 0,
        'index_dirty': _index_dirty,
        'threshold': settings.CLASSIFIER_THRESHOLD,
    }
