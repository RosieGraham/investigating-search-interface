"""
Phase 6: would a different encoder do better inside 512MB, and is the
multilingual claim supportable?

Benchmarks candidate sentence encoders on the paraphrase probe under the
production blended scoring (alpha 0.35), each with its own tokenizer and
its canonical pooling (mean for the MiniLM family and gte, CLS for bge).
Reports accuracy@1, coverage, false positives, index build time, per-query
latency, file size and process RSS. Also runs French, Spanish and
Portuguese paraphrases against the current English-only production model
and against a quantised multilingual candidate, because the funding case
currently claims multilingual potential and that claim deserves a number
rather than a hope.

    DJANGO_SETTINGS_MODULE=core.settings python scripts/benchmark_encoders.py \
        --models-root /tmp/altmodels --current-dir /tmp/models \
        --out docs/evaluations/encoders-2026-07-10.json
"""

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django  # noqa: E402

django.setup()

from researchdata import evaluation  # noqa: E402
from researchdata.models import Topic  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

MULTILINGUAL_QUERIES = [
    ("pourquoi tout le monde utilise Google", "Concentration and defaults", "fr"),
    ("comment savoir si un site web est fiable", "Search literacy", "fr"),
    ("peut-on faire confiance au resume IA de Google", "AI Overviews and generative search", "fr"),
    ("buscador que no te rastrea", "Privacy-focused and alternative engines", "es"),
    ("ejemplos de sesgo algoritmico", "Algorithmic bias and how audits find it", "es"),
    ("por que todos usan Google", "Concentration and defaults", "es"),
    ("os resultados do Google sao confiaveis", "Search literacy", "pt"),
    ("motor de busca que nao rastreia", "Privacy-focused and alternative engines", "pt"),
]


class Encoder:
    def __init__(self, model_path, tokenizer_path, pooling="mean"):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        tok = Tokenizer.from_file(str(tokenizer_path))
        tok.enable_truncation(max_length=256)
        tok.enable_padding()
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(model_path), sess_options=so, providers=["CPUExecutionProvider"])
        self.tokenizer = tok
        self.pooling = pooling
        self.input_names = [i.name for i in self.session.get_inputs()]

    def encode(self, texts, batch_size=16):
        chunks = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            encodings = self.tokenizer.encode_batch([t if t else " " for t in batch])
            input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
            feeds = {"input_ids": input_ids, "attention_mask": attention_mask}
            if "token_type_ids" in self.input_names:
                feeds["token_type_ids"] = np.zeros_like(input_ids)
            hidden = self.session.run(None, feeds)[0]
            if self.pooling == "cls":
                pooled = hidden[:, 0, :]
            else:
                mask = attention_mask[:, :, None].astype(np.float32)
                pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
            norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
            chunks.append((pooled / norms).astype(np.float32))
        return np.vstack(chunks)


def corpus_rows():
    rows = []
    for t in Topic.objects.select_related("topic_group").all():
        rows.append((t.id, t.embedding_text, False))
        for q in (t.example_queries or []):
            rows.append((t.id, str(q).strip(), True))
    return rows


def blended_scores(corpus_matrix, rows, topic_order, query_vec, alpha=0.35):
    scores = corpus_matrix @ query_vec
    desc, ex_max = {}, {}
    for (tid, _t, is_ex), s in zip(rows, scores):
        if is_ex:
            ex_max[tid] = max(ex_max.get(tid, -1.0), float(s))
        else:
            desc[tid] = float(s)
    return np.array([
        alpha * desc[t] + (1 - alpha) * ex_max[t] if t in ex_max else desc[t]
        for t in topic_order])


def evaluate_encoder(enc, rows, topic_order, probe_rows, serveable, id_to_name):
    texts = [r[1] for r in rows]
    t0 = time.time()
    corpus_matrix = enc.encode(texts)
    build_s = time.time() - t0
    queries = [r.query for r in probe_rows]
    t0 = time.time()
    qvecs = enc.encode(queries)
    per_query = (time.time() - t0) / len(queries)

    cache = {}
    for row, qv in zip(probe_rows, qvecs):
        scores = blended_scores(corpus_matrix, rows, topic_order, qv)
        order = np.argsort(-scores)
        cache[row.query] = [(topic_order[int(i)], float(scores[int(i)])) for i in order]
    metrics = evaluation.evaluate(
        probe_rows, threshold=0.35, margin=0.0,
        ranker=lambda q: cache[q], serveable_ids=serveable, id_to_name=id_to_name,
    )["metrics"]
    return metrics, build_s, per_query, corpus_matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-root", required=True)
    parser.add_argument("--current-dir", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    root = Path(args.models_root)
    current = Path(args.current_dir)

    rows = corpus_rows()
    topic_order = sorted({r[0] for r in rows})
    probe_rows = evaluation.load_labelled_csv(DATA / "paraphrase-probe-2026-07-10.csv")
    serveable = evaluation.topics_with_approved_prompts()
    id_to_name = evaluation.topic_names_by_id()
    names = id_to_name

    candidates = [
        ("current multi-qa-MiniLM-L6 int8", current / "model_qint8_avx512_vnni.onnx",
         current / "tokenizer.json", "mean"),
        ("all-MiniLM-L12-v2 fp32", root / "minilm-l12/model.onnx",
         root / "minilm-l12/tokenizer.json", "mean"),
        ("bge-small-en-v1.5 fp32", root / "bge-small/model.onnx",
         root / "bge-small/tokenizer.json", "cls"),
        ("gte-small fp32", root / "gte-small/model.onnx",
         root / "gte-small/tokenizer.json", "mean"),
        ("paraphrase-multilingual-MiniLM-L12 int8", root / "multi-minilm-q8/model.onnx",
         root / "multi-minilm-q8/tokenizer.json", "mean"),
    ]

    result = {"encoders": {}, "multilingual": {}}
    matrices = {}
    encoders = {}
    for label, model_path, tok_path, pooling in candidates:
        if not model_path.exists():
            result["encoders"][label] = {"error": "not downloaded"}
            continue
        enc = Encoder(model_path, tok_path, pooling)
        metrics, build_s, per_query, matrix = evaluate_encoder(
            enc, rows, topic_order, probe_rows, serveable, id_to_name)
        matrices[label] = matrix
        encoders[label] = enc
        result["encoders"][label] = {
            "pooling": pooling,
            "file_mb": round(model_path.stat().st_size / 1e6, 1),
            "accuracy_at_1": metrics["accuracy_at_1"],
            "accuracy_at_1_raw": metrics["accuracy_at_1_raw"],
            "coverage": metrics["coverage"],
            "fp_neg": metrics["false_positive_negative_controls"],
            "fp_unc": metrics["false_positive_ordinary_uncovered"],
            "mrr": metrics["mrr"],
            "index_build_seconds": round(build_s, 2),
            "per_query_seconds": round(per_query, 4),
            "peak_rss_mb": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        }
        print(label, result["encoders"][label])

    # Multilingual honesty check on current model and the multilingual candidate
    for label in ["current multi-qa-MiniLM-L6 int8",
                  "paraphrase-multilingual-MiniLM-L12 int8"]:
        if label not in matrices:
            continue
        enc, matrix = encoders[label], matrices[label]
        out_rows = []
        hits = 0
        for query, expected, lang in MULTILINGUAL_QUERIES:
            qv = enc.encode([query])[0]
            scores = blended_scores(matrix, rows, topic_order, qv)
            order = np.argsort(-scores)
            top1_id = topic_order[int(order[0])]
            top1 = names.get(top1_id, "?")
            score = float(scores[int(order[0])])
            correct = top1 == expected
            hits += int(correct)
            out_rows.append({
                "query": query, "lang": lang, "expected": expected,
                "top1": top1, "score": round(score, 4),
                "above_threshold": score >= 0.35, "correct": correct,
            })
        result["multilingual"][label] = {
            "rows": out_rows,
            "top1_accuracy": round(hits / len(MULTILINGUAL_QUERIES), 4),
        }
        print(f"multilingual {label}: {hits}/{len(MULTILINGUAL_QUERIES)}")

    result["provenance"] = {
        "runtime": evaluation.runtime_descriptor(),
        "git_sha": evaluation.git_sha(),
        "note": ("English candidate corpora embedded with each encoder's own "
                 "tokenizer and canonical pooling; bge evaluated without its "
                 "retrieval instruction prefix (symmetric question-to-question "
                 "use). fp32 candidate latencies are upper bounds: int8 "
                 "quantisation would roughly halve them, as it does for the "
                 "current model."),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"written {args.out}")


if __name__ == "__main__":
    main()
