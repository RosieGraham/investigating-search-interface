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
PROMPT_INDEX_FILENAME = 'prompt_index.npz'
PROMPT_INDEX_META_FILENAME = 'prompt_index_meta.json'

MAX_TOKENS = 256

_lock = threading.Lock()
_session = None          # onnxruntime.InferenceSession
_tokenizer = None        # tokenizers.Tokenizer
_input_names = None      # model input names, detected from the session

# Topic index, multi-vector (INDEX_VERSION 2):
# each topic contributes one description-prose row plus one row per example
# query. Scoring blends them: alpha * desc + (1 - alpha) * max(example).
# A topic with no example queries scores on its description alone.
INDEX_VERSION = 2
_index_matrix = None     # np.ndarray (n_rows, 384), L2-normalised
_index_topic_ids = None  # list[int], UNIQUE topic ids in index order
_row_topic_pos = None    # np.ndarray (n_rows,), row -> position in _index_topic_ids
_row_is_example = None   # np.ndarray (n_rows,) bool, True for example-query rows
_index_dirty = True
# If WEB_CONCURRENCY is ever raised above 1, this process-global dirty flag
# will not propagate across gunicorn workers. Replace it with shared state
# (e.g. a Setting row holding a timestamp or fingerprint, compared per request).
_prompt_matrix = None    # np.ndarray (n_prompts, 384), L2-normalised
_prompt_index_ids = None  # list[int], row-aligned with _prompt_matrix
_prompt_index_dirty = True
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


def encode(texts, batch_size=16):
    """
    Embed a list of strings -> np.ndarray (n, 384), L2-normalised float32.
    Mean pooling over token embeddings, masked by attention.

    Encoded in small batches so peak memory stays flat as the corpus grows.
    Embedding every topic in a single batch pads all rows to the longest
    description and allocates one large activation tensor; on the free 512MB
    instance that spikes over the limit and the warm-up build OOMs (it did at
    214 topics once enough carried long descriptions). Per-item embeddings are
    independent (attention is within-sequence, padding is masked), so chunking
    is numerically identical to one batch, just with a bounded memory peak.
    """
    _load_runtime()
    chunks = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start:start + batch_size]
        encodings = _tokenizer.encode_batch([t if t else ' ' for t in batch])
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
        chunks.append((pooled / norms).astype(np.float32))
    if not chunks:
        return np.zeros((0, 384), dtype=np.float32)
    return np.vstack(chunks)


def _topics_fingerprint(rows, model_id):
    """Hash everything the index is built from: description prose AND
    example queries (a stale on-disk index reused after an example-query
    edit would silently serve old matching), plus the index schema version
    so an old single-vector cache can never satisfy a multi-vector reader."""
    payload = (json.dumps([(r[0], r[1], r[2]) for r in rows], ensure_ascii=False)
               + f'|{model_id}|v{INDEX_VERSION}')
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def mark_index_dirty(*args, **kwargs):
    """Signal receiver: a Topic changed, rebuild the topic index on next use."""
    global _index_dirty
    _index_dirty = True


def mark_prompt_index_dirty(*args, **kwargs):
    """Signal receiver: a Prompt changed, rebuild the prompt index on next use."""
    global _prompt_index_dirty
    _prompt_index_dirty = True


def build_topic_index(force=False):
    """
    Embed all topics as multiple vectors each and cache in memory + on disk.

    Per topic: one row for the description prose (or "group: name" when
    undescribed), plus one row per example query. Returns
    (matrix, topic_ids). Raises ClassifierUnavailable if no runtime.
    """
    global _index_matrix, _index_topic_ids, _row_topic_pos, _row_is_example, _index_dirty
    from .models import Topic

    rows = [
        (t.id, t.embedding_text, list(t.example_queries or []))
        for t in Topic.objects.select_related('topic_group').all()
    ]
    if not rows:
        _index_matrix, _index_topic_ids, _index_dirty = None, [], False
        _row_topic_pos, _row_is_example = None, None
        return None, []

    fingerprint = _topics_fingerprint(rows, settings.EMBEDDING_MODEL_ID)
    index_path = _model_dir() / INDEX_FILENAME
    meta_path = _model_dir() / INDEX_META_FILENAME

    # Reuse the on-disk index when nothing has changed
    if not force and index_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get('fingerprint') == fingerprint and meta.get('version') == INDEX_VERSION:
                data = np.load(index_path)
                _index_matrix = data['matrix']
                _index_topic_ids = data['topic_ids'].tolist()
                _row_topic_pos = data['row_topic_pos']
                _row_is_example = data['row_is_example'].astype(bool)
                _index_dirty = False
                logger.info('Topic index loaded from disk (%d topics, %d rows).',
                            len(_index_topic_ids), _index_matrix.shape[0])
                return _index_matrix, _index_topic_ids
        except Exception as e:  # corrupt cache: rebuild
            logger.warning('Could not reuse topic index cache (%s); rebuilding.', e)

    texts, row_topic_pos, row_is_example = [], [], []
    topic_ids = []
    for pos, (topic_id, desc_text, examples) in enumerate(rows):
        topic_ids.append(topic_id)
        texts.append(desc_text)
        row_topic_pos.append(pos)
        row_is_example.append(False)
        for query in examples:
            if query and str(query).strip():
                texts.append(str(query).strip())
                row_topic_pos.append(pos)
                row_is_example.append(True)

    logger.info('Building topic index: embedding %d rows for %d topics...',
                len(texts), len(topic_ids))
    matrix = encode(texts)
    row_topic_pos = np.array(row_topic_pos, dtype=np.int64)
    row_is_example = np.array(row_is_example, dtype=bool)
    try:
        np.savez(
            index_path,
            matrix=matrix,
            topic_ids=np.array(topic_ids, dtype=np.int64),
            row_topic_pos=row_topic_pos,
            row_is_example=row_is_example.astype(np.int8),
        )
        meta_path.write_text(json.dumps({
            'fingerprint': fingerprint,
            'version': INDEX_VERSION,
            'topics': len(topic_ids),
            'rows': int(matrix.shape[0]),
        }))
    except OSError as e:
        logger.warning('Topic index not persisted (%s); in-memory only.', e)
    _index_matrix, _index_topic_ids = matrix, topic_ids
    _row_topic_pos, _row_is_example = row_topic_pos, row_is_example
    _index_dirty = False
    logger.info('Topic index built (%d topics, %d rows).', len(topic_ids), matrix.shape[0])
    return matrix, topic_ids


def _ensure_index():

    if _index_dirty or _index_matrix is None:
        with _lock:
            if _index_dirty or _index_matrix is None:
                build_topic_index()
    return _index_matrix, _index_topic_ids


def _blended_topic_scores(query_vec):
    """Scores for every topic: alpha * desc + (1 - alpha) * max(example).

    Topics without example queries score on their description alone. The
    blend keeps the description as an anchor so a single stray example
    query cannot capture a neighbourhood on its own, which pure max-pooling
    measurably does (it fires on negative controls; see the ablation tables
    in docs/matching-quality-report.md).
    """
    from .classifier_config import get_classifier_blend_alpha

    matrix, topic_ids = _ensure_index()
    if matrix is None or not topic_ids:
        return None, []
    alpha = get_classifier_blend_alpha()

    row_scores = matrix @ query_vec
    n_topics = len(topic_ids)

    desc_scores = np.empty(n_topics, dtype=np.float32)
    desc_rows = ~_row_is_example
    desc_scores[_row_topic_pos[desc_rows]] = row_scores[desc_rows]

    example_max = np.full(n_topics, -1.0, dtype=np.float32)
    if _row_is_example.any():
        np.maximum.at(example_max, _row_topic_pos[_row_is_example],
                      row_scores[_row_is_example])

    has_examples = example_max > -1.0
    blended = desc_scores.copy()
    blended[has_examples] = (alpha * desc_scores[has_examples]
                             + (1.0 - alpha) * example_max[has_examples])
    return blended, topic_ids


def embed_query(query_text):
    """
    Embed a single query string and return the L2-normalised (384,) vector.
    Callers that need both topic classification and prompt ranking can call
    this once, then pass the result to classify_query and rank_prompts,
    avoiding two encode() calls for the same text.
    Raises ClassifierUnavailable when the model cannot run.
    """
    _load_runtime()
    text = (query_text or '').strip() or ' '
    return encode([text])[0]


def classify_query(query_text, threshold=None, top_k=None, margin=None, _query_vec=None):
    """
    Return up to top_k (topic_id, confidence) pairs for a query, best first,
    all with confidence >= threshold. Empty list = no confident match.
    Raises ClassifierUnavailable when the model cannot run; callers are
    expected to catch it and use the trigger fallback.

    margin: required gap between the best and second-best topic before any
    match is returned. When the gap is smaller, the classifier abstains:
    scores that close are inside the pipeline's own numerical noise (int8
    quantisation and kernel differences move scores by more than 0.01), so
    the ranking between them is a coin toss, not a judgement. None reads
    the configured value; pass 0.0 to disable explicitly (the debug
    endpoint does, so editors can always see what the index thinks).

    _query_vec: optional pre-computed L2-normalised query embedding (384,).
    Pass the result of embed_query() to avoid re-encoding the same text.
    """
    if threshold is None:
        from .classifier_config import get_classifier_threshold
        threshold = get_classifier_threshold()
    if margin is None:
        from .classifier_config import get_classifier_margin
        margin = get_classifier_margin()
    if top_k is None:
        top_k = settings.CLASSIFIER_TOP_K

    query_text = (query_text or '').strip()
    if not query_text:
        return []

    q = _query_vec if _query_vec is not None else encode([query_text])[0]
    scores, topic_ids = _blended_topic_scores(q)
    if scores is None:
        return []

    order = np.argsort(-scores)
    if margin > 0 and len(order) > 1:
        gap = float(scores[order[0]]) - float(scores[order[1]])
        if gap < margin:
            return []
    return [
        (topic_ids[int(i)], float(scores[int(i)]))
        for i in order[:max(top_k, 1)]
        if float(scores[int(i)]) >= threshold
    ]


def _prompts_fingerprint(rows, model_id):
    payload = json.dumps([(r[0], r[1]) for r in rows], ensure_ascii=False) + '|' + model_id
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def build_prompt_index(force=False):
    """
    Embed all approved prompts and cache the matrix in memory and on disk.
    Analogous to build_topic_index. Returns (matrix, prompt_ids).
    Raises ClassifierUnavailable if no runtime.
    """
    global _prompt_matrix, _prompt_index_ids, _prompt_index_dirty
    from .models import Prompt

    rows = [
        (p['id'], p['prompt_content'])
        for p in Prompt.objects.filter(admin_approved=True).values('id', 'prompt_content').order_by('id')
    ]
    if not rows:
        _prompt_matrix, _prompt_index_ids, _prompt_index_dirty = None, [], False
        return None, []

    fingerprint = _prompts_fingerprint(rows, settings.EMBEDDING_MODEL_ID)
    index_path = _model_dir() / PROMPT_INDEX_FILENAME
    meta_path = _model_dir() / PROMPT_INDEX_META_FILENAME

    if not force and index_path.exists() and meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text())
            if meta.get('fingerprint') == fingerprint:
                data = np.load(index_path)
                _prompt_matrix = data['matrix']
                _prompt_index_ids = data['prompt_ids'].tolist()
                _prompt_index_dirty = False
                logger.info('Prompt index loaded from disk (%d prompts).', len(_prompt_index_ids))
                return _prompt_matrix, _prompt_index_ids
        except Exception as e:
            logger.warning('Could not reuse prompt index cache (%s); rebuilding.', e)

    logger.info('Building prompt index: embedding %d prompts...', len(rows))
    matrix = encode([r[1] for r in rows])
    prompt_ids = [r[0] for r in rows]
    try:
        np.savez(index_path, matrix=matrix, prompt_ids=np.array(prompt_ids, dtype=np.int64))
        meta_path.write_text(json.dumps({'fingerprint': fingerprint, 'count': len(prompt_ids)}))
    except OSError as e:
        logger.warning('Prompt index not persisted (%s); in-memory only.', e)
    _prompt_matrix, _prompt_index_ids, _prompt_index_dirty = matrix, prompt_ids, False
    logger.info('Prompt index built (%d prompts).', len(prompt_ids))
    return matrix, prompt_ids


def _ensure_prompt_index():
    if _prompt_index_dirty or _prompt_matrix is None:
        with _lock:
            if _prompt_index_dirty or _prompt_matrix is None:
                build_prompt_index()
    return _prompt_matrix, _prompt_index_ids


def rank_prompts(query_vec, candidate_ids):
    """
    Rank a list of prompt IDs by cosine similarity to query_vec, best first.
    Returns a list of (prompt_id, score) tuples.

    Prompts absent from the index (e.g. newly approved before the next
    rebuild) are appended at the end with score 0.0 so they are never
    silently dropped.
    """
    matrix, ids = _ensure_prompt_index()
    if matrix is None or not ids:
        return [(pid, 0.0) for pid in candidate_ids]

    id_to_row = {pid: i for i, pid in enumerate(ids)}
    ranked = []
    missing = []
    for pid in candidate_ids:
        row = id_to_row.get(pid)
        if row is not None:
            ranked.append((pid, float(matrix[row] @ query_vec)))
        else:
            missing.append((pid, 0.0))

    ranked.sort(key=lambda x: -x[1])
    return ranked + missing


def classifier_status():
    """Lightweight status dict for health checks and the admin."""
    from .classifier_config import (
        get_classifier_blend_alpha,
        get_classifier_margin,
        get_classifier_threshold,
    )

    model_present = (_model_dir() / MODEL_FILENAME).exists()
    return {
        'enabled': settings.CLASSIFIER_ENABLED,
        'model_id': settings.EMBEDDING_MODEL_ID,
        'model_present': model_present,
        'loaded': _session is not None,
        'index_topics': len(_index_topic_ids) if _index_topic_ids else 0,
        'index_rows': int(_index_matrix.shape[0]) if _index_matrix is not None else 0,
        'index_version': INDEX_VERSION,
        'index_dirty': _index_dirty,
        'threshold': get_classifier_threshold(),
        'margin': get_classifier_margin(),
        'blend_alpha': get_classifier_blend_alpha(),
    }
