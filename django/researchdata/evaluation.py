"""
Evaluation harness for the semantic matching layer.

Phase 1 of MATCHING-QUALITY-BRIEF.md: everything measurable about matching
quality runs through here, so every claim in the matching-quality report can
point at a table this module produced.

Design notes:
- The ranker is pluggable. evaluate() takes any callable
  `ranker(query_text) -> list[(topic_id, score)]` covering ALL topics,
  best first, unthresholded. The default production_ranker() reproduces the
  live scoring path exactly (same index, same encoder, same order). Phase 3
  ablations pass alternative rankers built from experimental representations,
  so baseline and experiment always share decision logic and metrics.
- The decision rule is threshold AND margin. Margin 0 reproduces current
  production behaviour (top1 >= threshold only).
- Serving is modelled faithfully, including the fall-through: production
  considers up to CLASSIFIER_TOP_K above-threshold topics in order and serves
  the first that has an approved prompt. A top-1 topic with no approved
  prompts (for example Vaccination) therefore does NOT produce a card unless
  a lower-ranked topic clears the threshold too. The harness must score
  "correctly silent" rows (expected_card=no) as correct.
- No HTTP anywhere. Offline, against the local database.
"""

import csv
import hashlib
import platform
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass
class LabelledRow:
    query: str
    expected_topic: str      # topic name, or "NONE"
    expected_card: bool
    category: str = ""
    query_shape: str = ""    # absorbed from future labelled sets; optional


@dataclass
class RowResult:
    query: str
    category: str
    query_shape: str
    expected_topic: str
    expected_card: bool
    top1_topic: str = ""
    top1_score: float = 0.0
    top2_topic: str = ""
    top2_score: float = 0.0
    margin: float = 0.0
    served_topic: str = ""   # empty string = no card served
    card_served: bool = False
    expected_rank: int = 0   # 1-based rank of expected topic; 0 = n/a or absent
    reciprocal_rank: float = 0.0
    correct: bool = False
    outcome: str = ""        # human-readable classification of the row

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def load_labelled_csv(path):
    """Read a labelled query CSV into LabelledRow objects.

    Required columns: query, expected_topic, expected_card.
    Optional columns (absorbed if present): category, query_shape.
    Unknown columns are ignored, so a richer future set loads without a
    rewrite. expected_topic values not present in the database are allowed
    and must not crash anything downstream.
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"query", "expected_topic", "expected_card"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Labelled CSV is missing columns: {sorted(missing)}")
        for raw in reader:
            if not (raw.get("query") or "").strip():
                continue
            rows.append(LabelledRow(
                query=raw["query"].strip(),
                expected_topic=(raw.get("expected_topic") or "NONE").strip(),
                expected_card=(raw.get("expected_card") or "").strip().lower() == "yes",
                category=(raw.get("category") or "").strip(),
                query_shape=(raw.get("query_shape") or "").strip(),
            ))
    return rows


def production_ranker(query_text):
    """Rank ALL topics for a query exactly as the live scoring path does.

    Same encoder, same cached index, same blended scoring as
    embedding.classify_query, but unthresholded, margin-free and over the
    full corpus so the harness can compute ranks and margins itself.
    """
    from .embedding import _blended_topic_scores, embed_query

    q = embed_query(query_text)
    scores, topic_ids = _blended_topic_scores(q)
    if scores is None:
        return []
    order = np.argsort(-scores)
    return [(topic_ids[int(i)], float(scores[int(i)])) for i in order]


def topics_with_approved_prompts():
    """Set of topic ids that can actually serve a card."""
    from .models import Prompt

    return set(
        Prompt.objects.filter(admin_approved=True).values_list("topic_id", flat=True)
    )


def topic_names_by_id():
    from .models import Topic

    return dict(Topic.objects.values_list("id", "name"))


def decide(ranked, threshold, margin, top_k, serveable_ids):
    """Apply the decision rule to a full ranking.

    Returns (served_topic_id_or_None, decision_note).
    Rule: abstain unless top1 >= threshold and (top1 - top2) >= margin.
    Then serve the first of the top_k above-threshold topics that has an
    approved prompt (production fall-through), else nothing.
    """
    if not ranked:
        return None, "empty-ranking"
    top1_score = ranked[0][1]
    top2_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top1_score < threshold:
        return None, "below-threshold"
    if (top1_score - top2_score) < margin:
        return None, "inside-margin"
    for topic_id, score in ranked[:top_k]:
        if score < threshold:
            break
        if topic_id in serveable_ids:
            return topic_id, "served"
    return None, "no-approved-prompts"


def evaluate(rows, threshold, margin=0.0, top_k=3, ranker=None,
             serveable_ids=None, id_to_name=None):
    """Run the labelled set through the decision rule and score it.

    Returns {"metrics": ..., "rows": [...], "confusion": [...]}.
    Every number the matching-quality report quotes should come from here.
    """
    ranker = ranker or production_ranker
    if serveable_ids is None:
        serveable_ids = topics_with_approved_prompts()
    if id_to_name is None:
        id_to_name = topic_names_by_id()
    name_to_id = {v: k for k, v in id_to_name.items()}

    results = []
    for row in rows:
        ranked = ranker(row.query)
        rr = RowResult(
            query=row.query, category=row.category, query_shape=row.query_shape,
            expected_topic=row.expected_topic, expected_card=row.expected_card,
        )
        if ranked:
            rr.top1_topic = id_to_name.get(ranked[0][0], str(ranked[0][0]))
            rr.top1_score = round(ranked[0][1], 4)
            if len(ranked) > 1:
                rr.top2_topic = id_to_name.get(ranked[1][0], str(ranked[1][0]))
                rr.top2_score = round(ranked[1][1], 4)
            rr.margin = round(rr.top1_score - rr.top2_score, 4)

        expected_id = name_to_id.get(row.expected_topic)
        if expected_id is not None:
            for i, (tid, _score) in enumerate(ranked, start=1):
                if tid == expected_id:
                    rr.expected_rank = i
                    rr.reciprocal_rank = round(1.0 / i, 4)
                    break

        served_id, note = decide(ranked, threshold, margin, top_k, serveable_ids)
        rr.card_served = served_id is not None
        rr.served_topic = id_to_name.get(served_id, "") if served_id else ""

        if row.expected_card:
            rr.correct = rr.card_served and rr.served_topic == row.expected_topic
            if rr.correct:
                rr.outcome = "correct-card"
            elif not rr.card_served:
                rr.outcome = f"missed ({note})"
            else:
                rr.outcome = "wrong-topic"
        else:
            rr.correct = not rr.card_served
            if row.expected_topic == "NONE":
                rr.outcome = "correct-silence" if rr.correct else "false-positive"
            else:
                # expected_card=no with a real expected topic: the
                # "are vaccines safe" shape. Silence is correct even though
                # the topic matches, because it has nothing approved to say.
                rr.outcome = "correct-silence" if rr.correct else "false-positive"
        results.append(rr)

    yes_rows = [r for r in results if r.expected_card]
    none_rows = [r for r in results
                 if not r.expected_card and r.expected_topic == "NONE"]
    neg_rows = [r for r in none_rows if r.category == "negative-control"]
    unc_rows = [r for r in none_rows if r.category != "negative-control"]
    nocard_rows = [r for r in results
                   if not r.expected_card and r.expected_topic != "NONE"]

    def _share(part, whole):
        return round(part / whole, 4) if whole else None

    metrics = {
        "n_rows": len(results),
        "n_expected_card": len(yes_rows),
        "accuracy_at_1": _share(sum(r.correct for r in yes_rows), len(yes_rows)),
        "accuracy_at_1_raw": _share(
            sum(r.top1_topic == r.expected_topic for r in yes_rows), len(yes_rows)),
        "coverage": _share(sum(r.card_served for r in yes_rows), len(yes_rows)),
        "false_positive_negative_controls":
            _share(sum(r.card_served for r in neg_rows), len(neg_rows)),
        "false_positive_ordinary_uncovered":
            _share(sum(r.card_served for r in unc_rows), len(unc_rows)),
        "no_card_expected_correct":
            _share(sum(r.correct for r in nocard_rows), len(nocard_rows)),
        "abstention_rate": _share(
            sum(not r.card_served for r in results), len(results)),
        "mrr": _share(sum(r.reciprocal_rank for r in yes_rows), len(yes_rows)),
        "threshold": threshold,
        "margin": margin,
        "top_k": top_k,
    }

    per_category = {}
    for r in results:
        cat = r.category or "uncategorised"
        bucket = per_category.setdefault(
            cat, {"n": 0, "correct": 0, "served": 0})
        bucket["n"] += 1
        bucket["correct"] += int(r.correct)
        bucket["served"] += int(r.card_served)
    metrics["per_category"] = per_category

    if any(r.query_shape for r in results):
        per_shape = {}
        for r in results:
            shape = r.query_shape or "unspecified"
            bucket = per_shape.setdefault(
                shape, {"n": 0, "correct": 0, "served": 0})
            bucket["n"] += 1
            bucket["correct"] += int(r.correct)
            bucket["served"] += int(r.card_served)
        metrics["per_query_shape"] = per_shape

    confusion = [
        r.as_dict() for r in results
        if (r.expected_card and not r.correct)
        or (not r.expected_card and r.card_served)
    ]
    return {
        "metrics": metrics,
        "rows": [r.as_dict() for r in results],
        "confusion": confusion,
    }


# ---------------------------------------------------------------------------
# Provenance helpers: a run that cannot say what produced it is not evidence.

def model_artifact_fingerprint():
    """Identify the actual ONNX file in use: name is always model.onnx, so
    hash the bytes. Cross-runtime comparisons need this."""
    from django.conf import settings

    from .embedding import MODEL_FILENAME

    path = Path(settings.EMBEDDING_MODEL_DIR) / MODEL_FILENAME
    if not path.exists():
        return "missing"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"sha256:{digest}:{path.stat().st_size}"


def runtime_descriptor():
    """Architecture and runtime version. Scores from a quantised encoder are
    properties of weights plus kernels plus architecture; two machines with
    the same weights can disagree by more than the tool's decision margins,
    so every persisted run records where its numbers came from."""
    try:
        import onnxruntime
        ort = onnxruntime.__version__
    except ImportError:
        ort = "unavailable"
    return f"onnxruntime {ort}; {platform.machine()}; python {platform.python_version()}"


def current_index_fingerprint():
    from django.conf import settings

    from .models import Topic
    from .embedding import _topics_fingerprint

    rows = [(t.id, t.embedding_text, list(t.example_queries or []))
            for t in Topic.objects.select_related("topic_group").all()]
    return _topics_fingerprint(rows, settings.EMBEDDING_MODEL_ID)


def git_sha():
    import subprocess

    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=Path(__file__).resolve().parents[2],
        ).stdout.strip()[:40] or "unknown"
    except Exception:
        return "unknown"
