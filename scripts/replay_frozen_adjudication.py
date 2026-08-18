#!/usr/bin/env python3
"""Replay the frozen adjudication table without rewriting expectations.

    python scripts/replay_frozen_adjudication.py \\
        --file release-evidence/season-2026/frozen-adjudication.csv \\
        --json-out release-evidence/season-2026/frozen-adjudication-replay.json

Reads the freeze, scores each row through the workshop top-one harness,
applies the release rubric, and writes verdicts beside the freeze. The
freeze file is never opened for write. A Blocker or High finding exits 2.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FREEZE = REPO_ROOT / "release-evidence" / "season-2026" / "frozen-adjudication.csv"
DEFAULT_OUT = REPO_ROOT / "release-evidence" / "season-2026" / "frozen-adjudication-replay.json"

sys.path.insert(0, str(REPO_ROOT / "django"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

import django  # noqa: E402

django.setup()

from researchdata.evaluation import (  # noqa: E402
    LabelledRow,
    current_index_fingerprint,
    evaluate,
    git_sha,
    model_artifact_fingerprint,
    runtime_descriptor,
)


def load_freeze(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def labelled_from_freeze(row):
    return LabelledRow(
        query=row["query"],
        expected_topic=(row.get("expected_topic") or "NONE").strip(),
        expected_card=(row.get("expected_card") or "").strip().lower() == "yes",
        category=(row.get("category") or "").strip(),
        query_shape=(row.get("query_shape") or "").strip(),
    )


def rubric_severity(frozen, result, error=None):
    if error:
        return "Blocker"
    if result.get("card_served") and result.get("served_topic") and result.get("top1_topic"):
        if result["served_topic"] != result["top1_topic"]:
            return "Blocker"
    expected_card = (frozen.get("expected_card") or "").strip().lower() == "yes"
    expected_topic = (frozen.get("expected_topic") or "NONE").strip()
    allowed = (frozen.get("allowed_equivalent") or "").strip()
    if expected_card:
        if not result.get("card_served"):
            return frozen.get("max_severity") or "Medium"
        served = result.get("served_topic") or ""
        if served == expected_topic or (allowed and served == allowed):
            return None
        return "High"
    if result.get("card_served"):
        return "High"
    return None


def replay_row(frozen, threshold, margin):
    labelled = labelled_from_freeze(frozen)
    try:
        out = evaluate([labelled], threshold=threshold, margin=margin)
        result = out["rows"][0]
        error = None
    except Exception as exc:
        result = {
            "query": frozen["query"],
            "card_served": False,
            "served_topic": "",
            "top1_topic": "",
            "top1_score": None,
            "outcome": f"error: {type(exc).__name__}",
        }
        error = type(exc).__name__
    severity = rubric_severity(frozen, result, error=error)
    return {
        "case_id": frozen.get("case_id"),
        "query": frozen["query"],
        "family": frozen.get("family"),
        "expected_topic": frozen.get("expected_topic"),
        "expected_card": frozen.get("expected_card"),
        "actual_top_topic": result.get("top1_topic") or "",
        "actual_top_score": result.get("top1_score"),
        "actual_served_topic": result.get("served_topic") or "",
        "actual_card": bool(result.get("card_served")),
        "harness_outcome": result.get("outcome"),
        "severity": severity,
        "error": error,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--margin", type=float, default=0.0)
    args = parser.parse_args(argv)

    freeze_path = args.file if args.file.is_absolute() else REPO_ROOT / args.file
    if not freeze_path.is_file():
        print(f"missing freeze: {freeze_path}", file=sys.stderr)
        return 1
    try:
        freeze_rel = str(freeze_path.relative_to(REPO_ROOT))
    except ValueError:
        freeze_rel = str(freeze_path)
    frozen_stat = freeze_path.stat()
    rows = load_freeze(freeze_path)
    verdicts = [replay_row(row, args.threshold, args.margin) for row in rows]
    blockers = [row for row in verdicts if row["severity"] == "Blocker"]
    highs = [row for row in verdicts if row["severity"] == "High"]
    mediums = [row for row in verdicts if row["severity"] == "Medium"]
    lows = [row for row in verdicts if row["severity"] == "Low"]
    payload = {
        "replayed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freeze": freeze_rel,
        "freeze_sha256": __import__("hashlib").sha256(freeze_path.read_bytes()).hexdigest(),
        "freeze_mtime": int(frozen_stat.st_mtime),
        "threshold": args.threshold,
        "margin": args.margin,
        "n_rows": len(verdicts),
        "n_blocker": len(blockers),
        "n_high": len(highs),
        "n_medium": len(mediums),
        "n_low": len(lows),
        "n_pass": sum(1 for row in verdicts if row["severity"] is None),
        "provenance": {
            "git_sha": git_sha(),
            "model_artifact": model_artifact_fingerprint(),
            "runtime": runtime_descriptor(),
            "index_fingerprint": current_index_fingerprint(),
        },
        "blockers": blockers,
        "highs": highs,
        "mediums": mediums,
        "verdicts": verdicts,
        "note": "Expectations were not rewritten after this run.",
    }
    out = args.json_out if args.json_out.is_absolute() else REPO_ROOT / args.json_out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    print(f"n={payload['n_rows']} pass={payload['n_pass']} medium={payload['n_medium']} high={payload['n_high']} blocker={payload['n_blocker']}")
    if blockers or highs:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
