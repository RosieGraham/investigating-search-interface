"""
Phase 3 ablation study: how should a topic become vectors?

Runs entirely offline against the local database and the labelled query set,
using the Phase 1 harness for every number, so baseline and experiments share
identical decision logic and metrics.

Representations compared (open questions 1 to 3 of the matching brief):
  baseline        one vector per topic from the full description blob
                  (contrasts included), exactly as production embeds today
  clean_desc      one vector from prose only (contrasts and example
                  queries stripped): the Q2 contrasts ablation at desc level
  max_pool        max(cos(desc_clean), max_i cos(example_i))
  mean_pool       mean over desc_clean and example vectors
  examples_only   max over example vectors (desc excluded)
  blend_a{a}      a * desc + (1 - a) * max_example, a swept
  topk2           0.5 * desc + 0.5 * mean(top 2 example cosines)
  discount_d{d}   max(desc, d * max_example), d swept: example queries
                  count, but never quite as much as the description
  cap_c{c}        max(desc, min(max_example, desc + c)): an example query
                  may lift a topic at most c above its description score
  *_noinherit     winner variants with the 176 undescribed topics excluded
                  from the index entirely (open question 3)

Usage:
    DJANGO_SETTINGS_MODULE=core.settings python scripts/ablation_study.py \
        [--out docs/evaluations/ablations-<date>.json]
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
from researchdata.diagnostics import parse_example_queries  # noqa: E402
from researchdata.embedding import encode  # noqa: E402
from researchdata.models import Topic  # noqa: E402

LABELLED = Path(__file__).resolve().parents[1] / "data" / "labelled-queries-2026-07-09.csv"
PACKAGE = Path(__file__).resolve().parents[1] / "data" / "content-package-2026-07-09.json"

ACCEPTANCE_QUERIES = [
    "why does everyone use Google",
    "how do I know if a website is reliable",
    "how much does Google pay Apple",
    "why are product review sites all the same",
    "lateral reading",
    "how to remove my name from Google",
    "why does the algorithm show me this",
    "why is reject all so hard to find",
    "are vaccines safe",
    "best multivitamin reddit",
]


def strip_contrasts(description):
    """Prose only: cut 'Example queries: ...' and 'Distinct from ...'."""
    text = description.strip()
    for marker in ("Example queries:", "Distinct from"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx].rstrip()
    return text


def build_topic_records():
    """One record per topic: prose, examples, name_text, full blob."""
    package_examples = {}
    if PACKAGE.exists():
        pkg = json.loads(PACKAGE.read_text(encoding="utf-8"))
        package_examples = {
            t["name"]: t["example_queries"] for t in pkg.get("topics", [])
        }
    records = []
    parse_mismatches = []
    for topic in Topic.objects.select_related("topic_group").all():
        name_text = f"{topic.topic_group.name}: {topic.name}"
        if topic.description and topic.description.strip():
            blob = topic.description.strip()
            prose = strip_contrasts(blob)
            parsed = parse_example_queries(blob)
            authoritative = package_examples.get(topic.name)
            if authoritative is not None and parsed != authoritative:
                parse_mismatches.append(
                    {"topic": topic.name, "parsed": parsed, "package": authoritative})
            examples = authoritative if authoritative is not None else parsed
            records.append({
                "id": topic.id, "name": topic.name, "described": True,
                "blob": blob, "prose": prose, "examples": examples,
                "name_text": name_text,
            })
        else:
            records.append({
                "id": topic.id, "name": topic.name, "described": False,
                "blob": name_text, "prose": name_text, "examples": [],
                "name_text": name_text,
            })
    return records, parse_mismatches


def embed_all(records, queries):
    """Embed every distinct text once; return lookup dict."""
    texts = set(queries)
    for r in records:
        texts.add(r["blob"])
        texts.add(r["prose"])
        texts.update(r["examples"])
    texts = sorted(texts)
    vectors = encode(texts)
    return {t: vectors[i] for i, t in enumerate(texts)}


def make_ranker(records, vec, mode, include_undescribed=True, **kw):
    """Build ranker(query) for a representation mode."""
    active = [r for r in records if include_undescribed or r["described"]]

    def score_topic(r, qv):
        desc = float(vec[r["prose"]] @ qv)
        if mode == "baseline":
            return float(vec[r["blob"]] @ qv)
        if mode == "clean_desc" or not r["examples"]:
            return desc
        ex = [float(vec[e] @ qv) for e in r["examples"]]
        mx = max(ex)
        if mode == "max_pool":
            return max(desc, mx)
        if mode == "mean_pool":
            return float(np.mean([desc] + ex))
        if mode == "examples_only":
            return mx
        if mode == "blend":
            a = kw["a"]
            return a * desc + (1 - a) * mx
        if mode == "topk2":
            top2 = sorted(ex, reverse=True)[:2]
            return 0.5 * desc + 0.5 * float(np.mean(top2))
        if mode == "discount":
            return max(desc, kw["d"] * mx)
        if mode == "cap":
            return max(desc, min(mx, desc + kw["c"]))
        raise ValueError(mode)

    def ranker(query):
        qv = vec[query]
        scored = [(r["id"], score_topic(r, qv)) for r in active]
        scored.sort(key=lambda x: -x[1])
        return scored

    return ranker


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=None)
    parser.add_argument("--threshold", type=float, default=0.35)
    args = parser.parse_args()

    rows = evaluation.load_labelled_csv(LABELLED)
    queries = [r.query for r in rows]
    records, mismatches = build_topic_records()
    if mismatches:
        print(f"WARNING: parser disagrees with package example_queries on "
              f"{len(mismatches)} topics; using package lists for those.")
        for m in mismatches[:5]:
            print("  ", m["topic"])
    print(f"{len(records)} topics ({sum(r['described'] for r in records)} described), "
          f"embedding all distinct texts once...")
    vec = embed_all(records, queries)
    print(f"{len(vec)} vectors ready.")

    serveable = evaluation.topics_with_approved_prompts()
    id_to_name = evaluation.topic_names_by_id()

    configs = [
        ("baseline", dict(mode="baseline")),
        ("clean_desc", dict(mode="clean_desc")),
        ("max_pool", dict(mode="max_pool")),
        ("mean_pool", dict(mode="mean_pool")),
        ("examples_only", dict(mode="examples_only")),
        ("blend_a0.3", dict(mode="blend", a=0.3)),
        ("blend_a0.5", dict(mode="blend", a=0.5)),
        ("blend_a0.7", dict(mode="blend", a=0.7)),
        ("topk2", dict(mode="topk2")),
        ("discount_d0.85", dict(mode="discount", d=0.85)),
        ("discount_d0.90", dict(mode="discount", d=0.90)),
        ("discount_d0.95", dict(mode="discount", d=0.95)),
        ("cap_c0.05", dict(mode="cap", c=0.05)),
        ("cap_c0.10", dict(mode="cap", c=0.10)),
        ("max_pool_noinherit", dict(mode="max_pool", include_undescribed=False)),
        ("baseline_noinherit", dict(mode="baseline", include_undescribed=False)),
    ]

    results = {}
    table = []
    for label, cfg in configs:
        ranker = make_ranker(records, vec, **cfg)
        out = evaluation.evaluate(
            rows, threshold=args.threshold, margin=0.0,
            ranker=ranker, serveable_ids=serveable, id_to_name=id_to_name)
        m = out["metrics"]
        acceptance = {
            r["query"]: {
                "top1": r["top1_topic"], "served": r["served_topic"] or None,
                "score": r["top1_score"], "margin": r["margin"],
                "outcome": r["outcome"],
            }
            for r in out["rows"] if r["query"] in ACCEPTANCE_QUERIES
        }
        results[label] = {"metrics": m, "acceptance": acceptance,
                          "confusion": out["confusion"]}
        table.append((label, m["accuracy_at_1"], m["accuracy_at_1_raw"],
                      m["coverage"], m["false_positive_negative_controls"],
                      m["false_positive_ordinary_uncovered"], m["mrr"]))
        print(f"{label:22s} acc@1={m['accuracy_at_1']:.4f} raw={m['accuracy_at_1_raw']:.4f} "
              f"cov={m['coverage']:.4f} FPneg={m['false_positive_negative_controls']} "
              f"FPunc={m['false_positive_ordinary_uncovered']} mrr={m['mrr']:.4f}")

    if args.out:
        payload = {
            "threshold": args.threshold,
            "labelled_file": LABELLED.name,
            "provenance": {
                "model_artifact": evaluation.model_artifact_fingerprint(),
                "runtime": evaluation.runtime_descriptor(),
                "git_sha": evaluation.git_sha(),
            },
            "parse_mismatches": mismatches,
            "results": results,
        }
        Path(args.out).write_text(json.dumps(payload, indent=1), encoding="utf-8")
        print(f"written {args.out}")


if __name__ == "__main__":
    main()
