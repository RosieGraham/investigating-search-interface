"""
Phase 4: sweep the decision rule (threshold x margin) on the evaluation
harness, under the production blended scoring, for both instruments (the
2026-07-09 labelled set and the 2026-07-10 paraphrase probe).

Also runs the open-question-4 variant: a normalised decision signal
(top1 minus corpus mean similarity) compared against the plain top1
threshold at matched false-positive levels.

    DJANGO_SETTINGS_MODULE=core.settings python scripts/decision_grid.py \
        --out docs/evaluations/decision-grid-2026-07-10.json
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django  # noqa: E402

django.setup()

from researchdata import evaluation  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"

THRESHOLDS = [round(0.30 + 0.025 * i, 3) for i in range(11)]  # 0.30 .. 0.55
MARGINS = [0.0, 0.01, 0.02, 0.03, 0.05, 0.08]


def precomputed_ranker(rows):
    """Rank each query once; grid cells reuse the ranking."""
    cache = {}
    for row in rows:
        cache[row.query] = evaluation.production_ranker(row.query)
    return lambda q: cache[q]


def sweep(rows, ranker, serveable, id_to_name):
    grid = []
    for threshold in THRESHOLDS:
        for margin in MARGINS:
            m = evaluation.evaluate(
                rows, threshold=threshold, margin=margin,
                ranker=ranker, serveable_ids=serveable, id_to_name=id_to_name,
            )["metrics"]
            grid.append({
                "threshold": threshold,
                "margin": margin,
                "accuracy_at_1": m["accuracy_at_1"],
                "coverage": m["coverage"],
                "fp_neg": m["false_positive_negative_controls"],
                "fp_unc": m["false_positive_ordinary_uncovered"],
                "abstention": m["abstention_rate"],
            })
    return grid


def normalised_signal_study(rows, ranker, id_to_name, serveable):
    """Open question 4: decide on (top1 - mean all-topic similarity).

    For each row compute both signals; report the separability of
    positives vs negative controls under each, as the area where a cutoff
    achieves FP=0, and the coverage each achieves at its best FP=0 cutoff.
    """
    signals = []
    for row in rows:
        ranked = ranker(row.query)
        scores = np.array([s for _, s in ranked])
        top1 = float(scores[0])
        norm = top1 - float(scores.mean())
        signals.append({
            "query": row.query,
            "positive": row.expected_card,
            "negative_control": row.category == "negative-control",
            "top1": round(top1, 4),
            "normalised": round(norm, 4),
        })
    out = {"rows": signals}
    for key in ("top1", "normalised"):
        neg_max = max(s[key] for s in signals if s["negative_control"])
        pos = sorted(s[key] for s in signals if s["positive"])
        coverage_at_fp0 = sum(1 for p in pos if p > neg_max) / len(pos) if pos else None
        out[f"{key}_neg_max"] = round(neg_max, 4)
        out[f"{key}_coverage_at_fp0"] = round(coverage_at_fp0, 4)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    serveable = evaluation.topics_with_approved_prompts()
    id_to_name = evaluation.topic_names_by_id()

    result = {"thresholds": THRESHOLDS, "margins": MARGINS, "sets": {}}
    for label, filename in [
        ("labelled-2026-07-09", "labelled-queries-2026-07-09.csv"),
        ("paraphrase-probe-2026-07-10", "paraphrase-probe-2026-07-10.csv"),
    ]:
        rows = evaluation.load_labelled_csv(DATA / filename)
        ranker = precomputed_ranker(rows)
        grid = sweep(rows, ranker, serveable, id_to_name)
        norm = normalised_signal_study(rows, ranker, id_to_name, serveable)
        result["sets"][label] = {"grid": grid, "normalised_study": norm}

        print(f"\n=== {label} ===")
        print("thr    " + "  ".join(f"m={m:<5}" for m in MARGINS))
        for threshold in THRESHOLDS:
            cells = []
            for margin in MARGINS:
                cell = next(g for g in grid
                            if g["threshold"] == threshold and g["margin"] == margin)
                flag = "*" if (cell["fp_neg"] == 0 and cell["fp_unc"] == 0) else "!"
                cells.append(f"{cell['coverage']:.2f}/{cell['accuracy_at_1']:.2f}{flag}")
            print(f"{threshold:<6} " + "  ".join(cells))
        print(f"normalised: neg_max top1={norm['top1_neg_max']} cov@FP0={norm['top1_coverage_at_fp0']} | "
              f"norm={norm['normalised_neg_max']} cov@FP0={norm['normalised_coverage_at_fp0']}")

    result["provenance"] = {
        "model_artifact": evaluation.model_artifact_fingerprint(),
        "runtime": evaluation.runtime_descriptor(),
        "git_sha": evaluation.git_sha(),
        "scorer": "production blended (alpha 0.35)",
    }
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"\nwritten {args.out}")


if __name__ == "__main__":
    main()
