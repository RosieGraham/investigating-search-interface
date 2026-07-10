"""
Phase 5: is the displayed "match percentage" honest, and what would be?

Takes every (query, served-or-would-serve) pair from both evaluation
instruments, treats "the served topic was the expected topic" as the
correctness label, and asks how well the blended cosine score predicts it:
reliability bins, then Platt scaling (logistic, fitted by Newton steps) and
isotonic regression (pool-adjacent-violators), with expected calibration
error before and after. Small n is reported alongside every number; the
point is the shape of the answer, not the third decimal.

    DJANGO_SETTINGS_MODULE=core.settings python scripts/calibration_study.py \
        --out docs/evaluations/calibration-2026-07-10.json
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


def collect_pairs():
    """(top1_score, correct) for every row that produced a top-1 candidate."""
    serveable = evaluation.topics_with_approved_prompts()
    id_to_name = evaluation.topic_names_by_id()
    pairs = []
    for filename, weight in [
        ("labelled-queries-2026-07-09.csv", "contaminated"),
        ("paraphrase-probe-2026-07-10.csv", "probe"),
    ]:
        rows = evaluation.load_labelled_csv(DATA / filename)
        out = evaluation.evaluate(rows, threshold=0.0, margin=0.0,
                                  serveable_ids=serveable, id_to_name=id_to_name)
        for r in out["rows"]:
            if not r["top1_topic"]:
                continue
            correct = (r["top1_topic"] == r["expected_topic"]) and r["expected_card"]
            pairs.append({
                "set": weight,
                "query": r["query"],
                "score": r["top1_score"],
                "correct": bool(correct),
            })
    return pairs


def reliability(scores, labels, n_bins=6):
    edges = np.linspace(scores.min(), scores.max() + 1e-9, n_bins + 1)
    bins = []
    for i in range(n_bins):
        mask = (scores >= edges[i]) & (scores < edges[i + 1])
        if mask.sum() == 0:
            continue
        bins.append({
            "lo": round(float(edges[i]), 4),
            "hi": round(float(edges[i + 1]), 4),
            "n": int(mask.sum()),
            "mean_score": round(float(scores[mask].mean()), 4),
            "empirical_accuracy": round(float(labels[mask].mean()), 4),
        })
    return bins


def ece(scores, labels, probs, n_bins=6):
    edges = np.linspace(0, 1 + 1e-9, n_bins + 1)
    total = 0.0
    for i in range(n_bins):
        mask = (probs >= edges[i]) & (probs < edges[i + 1])
        if mask.sum() == 0:
            continue
        total += (mask.sum() / len(probs)) * abs(
            float(labels[mask].mean()) - float(probs[mask].mean()))
    return round(float(total), 4)


def platt(scores, labels, iters=200):
    """Logistic fit p = sigmoid(a * s + b) by Newton-Raphson."""
    a, b = 1.0, 0.0
    s, y = scores.astype(np.float64), labels.astype(np.float64)
    for _ in range(iters):
        z = a * s + b
        p = 1.0 / (1.0 + np.exp(-z))
        g_a = float(((p - y) * s).sum())
        g_b = float((p - y).sum())
        w = p * (1 - p)
        h_aa = float((w * s * s).sum()) + 1e-9
        h_ab = float((w * s).sum())
        h_bb = float(w.sum()) + 1e-9
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-10:
            break
    return a, b


def isotonic(scores, labels):
    """Pool-adjacent-violators; returns step function knots."""
    order = np.argsort(scores)
    s, y = scores[order], labels[order].astype(np.float64)
    level = list(y)
    weight = [1.0] * len(y)
    start = list(range(len(y)))
    i = 0
    values = level[:]
    # PAVA merge
    blocks = [[y[k], 1.0, s[k], s[k]] for k in range(len(y))]
    merged = []
    for block in blocks:
        merged.append(block)
        while len(merged) > 1 and merged[-2][0] > merged[-1][0]:
            b2 = merged.pop()
            b1 = merged.pop()
            total_weight = b1[1] + b2[1]
            merged.append([
                (b1[0] * b1[1] + b2[0] * b2[1]) / total_weight,
                total_weight, b1[2], b2[3],
            ])
    return [{"score_lo": round(float(b[2]), 4), "score_hi": round(float(b[3]), 4),
             "prob": round(float(b[0]), 4)} for b in merged]


def apply_isotonic(knots, scores):
    probs = np.zeros(len(scores))
    for i, s in enumerate(scores):
        prob = knots[0]["prob"]
        for k in knots:
            if s >= k["score_lo"]:
                prob = k["prob"]
        probs[i] = prob
    return probs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    pairs = collect_pairs()
    probe = [p for p in pairs if p["set"] == "probe"]
    scores = np.array([p["score"] for p in probe])
    labels = np.array([p["correct"] for p in probe])

    result = {
        "n_pairs_probe": len(probe),
        "n_pairs_total": len(pairs),
        "note": ("Fitted on the paraphrase probe only: the 2026-07-09 set's "
                 "positives are verbatim example queries and would teach the "
                 "calibrator that high scores are always right."),
        "reliability_raw": reliability(scores, labels),
        "ece_raw_as_displayed": ece(scores, labels, np.clip(scores, 0, 1)),
    }

    a, b = platt(scores, labels)
    platt_probs = 1.0 / (1.0 + np.exp(-(a * scores + b)))
    result["platt"] = {"a": round(a, 4), "b": round(b, 4),
                       "ece": ece(scores, labels, platt_probs)}

    knots = isotonic(scores, labels)
    iso_probs = apply_isotonic(knots, scores)
    result["isotonic"] = {"knots": knots, "ece": ece(scores, labels, iso_probs)}

    for name, mapper in [
        ("raw", lambda s: s),
        ("platt", lambda s: 1.0 / (1.0 + np.exp(-(a * s + b)))),
    ]:
        example_scores = [0.35, 0.45, 0.55, 0.65]
        result[f"display_examples_{name}"] = {
            str(s): round(float(mapper(s)), 3) for s in example_scores}

    print(json.dumps(result, indent=1))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"written {args.out}")


if __name__ == "__main__":
    main()
