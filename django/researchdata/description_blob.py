"""
Splitting and recombining the legacy single-blob topic description.

Blob shape (every described topic in the corpus follows it, verified
byte-exact on all 52 on 10 July 2026):

    <prose> Example queries: a, b, c. Distinct from X (...), from Y (...).

The three parts do different jobs and belong in different fields:
prose describes (embedded), example queries anchor real phrasings
(embedded separately, one vector each), contrasts keep human editors from
writing overlapping topics (never embedded: putting a competitor's
vocabulary inside a topic's own vector is how "Children and search"
captured queries about Google's market share).
"""

import re

_SPLIT = re.compile(
    r"^(?P<prose>.*?)"
    r"(?:\s+(?P<examples>Example queries:.*?\.))?"
    r"(?:\s+(?P<contrasts>Distinct from .*))?$",
    re.S,
)
_EXAMPLES_PREFIX = "Example queries:"


def split_blob(blob):
    """Split a legacy blob into (prose, example_queries, contrasts).

    Returns (prose: str, example_queries: list[str], contrasts: str).
    Raises ValueError when the blob does not recombine byte-exactly, so a
    caller (especially the data migration) can fail loudly rather than
    silently mangle content.
    """
    blob = (blob or "").strip()
    if not blob:
        return "", [], ""
    match = _SPLIT.match(blob)
    prose = match.group("prose") or ""
    examples_segment = match.group("examples") or ""
    contrasts = match.group("contrasts") or ""

    example_queries = []
    if examples_segment:
        inner = examples_segment[len(_EXAMPLES_PREFIX):].strip()
        if inner.endswith("."):
            inner = inner[:-1]
        example_queries = [q.strip() for q in inner.split(",") if q.strip()]

    rebuilt = recombine_blob(prose, example_queries, contrasts)
    if rebuilt != blob:
        raise ValueError(
            f"Blob does not recombine byte-exactly; refusing to split. "
            f"Original starts: {blob[:80]!r}")
    return prose, example_queries, contrasts


def recombine_blob(prose, example_queries, contrasts):
    """Rebuild the legacy blob from split fields (the reverse migration)."""
    parts = [prose] if prose else []
    if example_queries:
        parts.append(f"{_EXAMPLES_PREFIX} {', '.join(example_queries)}.")
    if contrasts:
        parts.append(contrasts)
    return " ".join(parts)
