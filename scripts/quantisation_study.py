"""
Phase 5b: is quantisation (and runtime) noise larger than the margins the
tool decides on?

Embeds the full multi-vector corpus and the paraphrase-probe queries under
every ONNX artifact the deploy script can download, computes blended topic
scores per (query, artifact), and reports: score-delta distributions
between artifacts, top-1 and top-3 rank flips, and how the deltas compare
with observed top1-top2 margins. Also times each artifact and records file
size and process memory as the cost side of the precision trade.

Context this study rides on: the same weights on a different CPU
architecture (this aarch64 sandbox vs x86 production) already disagree
with live scores by sd 0.014 with corpus and pipeline verified identical,
so the noise floor is a property of the deployment, not only of the
artifact choice.

    DJANGO_SETTINGS_MODULE=core.settings python scripts/quantisation_study.py \
        --models-dir /tmp/models --out docs/evaluations/quantisation-2026-07-10.json
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

ARTIFACTS = [
    "model_qint8_avx512_vnni.onnx",
    "model_quint8_avx2.onnx",
    "model_qint8_arm64.onnx",
    "model_O2.onnx",
    "model.onnx",
]


class RawEncoder:
    """The production pipeline against an arbitrary artifact file."""

    def __init__(self, model_path, tokenizer_path):
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


def blended(row_scores, rows, topic_order, alpha=0.35):
    desc = {}
    ex_max = {}
    for (tid, _text, is_ex), s in zip(rows, row_scores):
        if is_ex:
            ex_max[tid] = max(ex_max.get(tid, -1.0), float(s))
        else:
            desc[tid] = float(s)
    out = np.empty(len(topic_order), dtype=np.float64)
    for i, tid in enumerate(topic_order):
        d = desc[tid]
        out[i] = alpha * d + (1 - alpha) * ex_max[tid] if tid in ex_max else d
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    models_dir = Path(args.models_dir)

    rows = corpus_rows()
    topic_order = sorted({r[0] for r in rows})
    texts = [r[1] for r in rows]
    probe = evaluation.load_labelled_csv(DATA / "paraphrase-probe-2026-07-10.csv")
    queries = [r.query for r in probe]

    per_artifact = {}
    scores_by_artifact = {}
    for artifact in ARTIFACTS:
        path = models_dir / artifact
        if not path.exists():
            per_artifact[artifact] = {"error": "artifact file not present"}
            continue
        t0 = time.time()
        enc = RawEncoder(path, models_dir / "tokenizer.json")
        load_s = time.time() - t0
        t0 = time.time()
        corpus_matrix = enc.encode(texts)
        corpus_s = time.time() - t0
        t0 = time.time()
        query_vecs = enc.encode(queries)
        query_s = (time.time() - t0) / len(queries)
        qscores = np.stack([
            blended(corpus_matrix @ qv, rows, topic_order) for qv in query_vecs])
        scores_by_artifact[artifact] = qscores
        per_artifact[artifact] = {
            "file_mb": round(path.stat().st_size / 1e6, 1),
            "load_seconds": round(load_s, 2),
            "corpus_embed_seconds": round(corpus_s, 2),
            "per_query_seconds": round(query_s, 4),
            "peak_rss_mb": round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        }

    reference = "model.onnx"
    ref = scores_by_artifact[reference]
    comparisons = {}
    for artifact, qscores in scores_by_artifact.items():
        if artifact == reference:
            continue
        deltas = qscores - ref
        top1_flips = 0
        top3_changes = 0
        for i in range(len(queries)):
            ref_order = np.argsort(-ref[i])
            art_order = np.argsort(-qscores[i])
            if ref_order[0] != art_order[0]:
                top1_flips += 1
            if set(ref_order[:3]) != set(art_order[:3]):
                top3_changes += 1
        comparisons[artifact] = {
            "delta_mean": round(float(deltas.mean()), 4),
            "delta_sd": round(float(deltas.std()), 4),
            "delta_max_abs": round(float(np.abs(deltas).max()), 4),
            "top1_flips": top1_flips,
            "top3_set_changes": top3_changes,
            "n_queries": len(queries),
        }

    margins = []
    for i in range(len(queries)):
        order = np.argsort(-ref[i])
        margins.append(float(ref[i][order[0]] - ref[i][order[1]]))
    margins = np.array(margins)

    result = {
        "corpus_rows": len(rows),
        "topics": len(topic_order),
        "n_queries": len(queries),
        "reference_artifact": reference,
        "per_artifact": per_artifact,
        "comparisons_vs_fp32": comparisons,
        "margin_distribution_fp32": {
            "min": round(float(margins.min()), 4),
            "p25": round(float(np.percentile(margins, 25)), 4),
            "median": round(float(np.median(margins)), 4),
            "p75": round(float(np.percentile(margins, 75)), 4),
            "share_below_0.02": round(float((margins < 0.02).mean()), 4),
            "share_below_0.05": round(float((margins < 0.05).mean()), 4),
        },
        "cross_runtime_note": (
            "Independently measured on 2026-07-10: identical weights and "
            "corpus on aarch64 vs the x86 live service disagree by sd 0.014 "
            "(max 0.06) per score, flipping 8 of 51 labelled top-1s, all at "
            "margins below 0.033."),
        "provenance": {
            "runtime": evaluation.runtime_descriptor(),
            "git_sha": evaluation.git_sha(),
            "blend_alpha": 0.35,
        },
    }
    print(json.dumps(result, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"written {args.out}")


if __name__ == "__main__":
    main()
