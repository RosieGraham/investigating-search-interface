"""
Matching diagnostics (Phase 2 of MATCHING-QUALITY-BRIEF.md).

Three instruments that explain matching behaviour rather than just scoring it:

- Attractor detector: pairwise topic confusability. Under the multi-vector
  index (INDEX_VERSION 2) two topics are confusable when ANY row of one sits
  close to ANY row of the other, so the detector reports the max cross-row
  similarity per pair. This is the check that would have caught two topics
  quietly absorbing seven others' traffic.
- Self-retrieval: does a topic retrieve its own example queries at rank 1
  under the production blended scoring? NOTE the semantics under the
  multi-vector index: an indexed topic contains its own example-query
  vectors, so its blended score on those exact queries includes a
  cosine-1.0 term and self-retrieval passes nearly by construction. The
  check keeps its bite for content that is NOT yet indexed, which is how
  the pre-apply guard uses it: score a candidate topic's queries against
  the EXISTING index and flag whatever captures them.
- Out-of-domain scores: what the corpus-max blended score looks like for
  queries that should match nothing. The gap (or overlap) between that
  distribution and genuine queries decides whether an abstention rule can
  work.

All functions run against the current production index and scoring path, so
their numbers are the live tool's numbers.
"""

import re

import numpy as np

EXAMPLE_QUERIES_PATTERN = re.compile(
    r"Example queries:\s*(?P<queries>.+?)(?:\.\s*Distinct from|\.?\s*$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_example_queries(description):
    """Extract the example-query list from a legacy single-blob description.

    Post-migration, Topic.example_queries is the source of truth; this
    parser remains for pre-migration data and for reading candidate
    packages that still carry blobs.
    """
    if not description:
        return []
    match = EXAMPLE_QUERIES_PATTERN.search(description)
    if not match:
        return []
    raw = match.group("queries")
    queries = [q.strip().strip('"').strip("'") for q in raw.split(",")]
    return [q for q in queries if q]


def _index_arrays():
    from . import embedding

    embedding._ensure_index()
    if embedding._index_matrix is None:
        raise RuntimeError("Topic index is empty; nothing to diagnose.")
    return (embedding._index_matrix, list(embedding._index_topic_ids),
            embedding._row_topic_pos, embedding._row_is_example)


def attractor_pairs(top_n=25):
    """Most-confusable topic pairs: max cross-row cosine similarity.

    Reports, for each of the top_n pairs, which row kinds collided
    (desc-desc, desc-example, example-example): an example-example
    collision means two topics are claiming the same user phrasings, which
    is an editorial decision waiting to be made rather than a modelling
    problem.
    """
    from .models import Topic

    matrix, topic_ids, row_pos, row_is_ex = _index_arrays()
    names = dict(Topic.objects.values_list("id", "name"))
    described = {t.id: bool(t.description) for t in Topic.objects.only("id", "description")}

    sims = matrix @ matrix.T
    best = {}
    n_rows = matrix.shape[0]
    for i in range(n_rows):
        pi = int(row_pos[i])
        row = sims[i]
        for j in range(i + 1, n_rows):
            pj = int(row_pos[j])
            if pi == pj:
                continue
            key = (pi, pj) if pi < pj else (pj, pi)
            s = float(row[j])
            prev = best.get(key)
            if prev is None or s > prev[0]:
                kind = f"{'ex' if row_is_ex[i] else 'desc'}-{'ex' if row_is_ex[j] else 'desc'}"
                best[key] = (s, kind)

    pairs = []
    for (pi, pj), (s, kind) in sorted(best.items(), key=lambda kv: -kv[1][0])[:max(top_n, 1)]:
        a, b = topic_ids[pi], topic_ids[pj]
        pairs.append({
            "topic_a": names.get(a, str(a)),
            "topic_b": names.get(b, str(b)),
            "similarity": round(s, 4),
            "row_kinds": kind,
            "a_described": described.get(a, False),
            "b_described": described.get(b, False),
        })
    return pairs


def _blended_rank(query_text):
    """(scores, topic_ids) under the production blended scoring."""
    from .embedding import _blended_topic_scores, embed_query

    return _blended_topic_scores(embed_query(query_text))


def self_retrieval():
    """For every topic with example queries: rank of the owner topic when
    each of its example queries is issued, under production scoring."""
    from .models import Topic

    rows = []
    summary = []
    names = dict(Topic.objects.values_list("id", "name"))
    for topic in Topic.objects.exclude(description__isnull=True).exclude(description=""):
        queries = list(topic.example_queries or [])
        if not queries:
            queries = parse_example_queries(topic.description)
        if not queries:
            continue
        failures = 0
        for query in queries:
            scores, topic_ids = _blended_rank(query)
            order = np.argsort(-scores)
            ranked_ids = [topic_ids[int(i)] for i in order]
            if topic.id not in ranked_ids:
                continue
            rank = ranked_ids.index(topic.id) + 1
            own = float(scores[topic_ids.index(topic.id)])
            top1_id = ranked_ids[0]
            margin = own - float(scores[topic_ids.index(top1_id)]) if rank != 1 else (
                own - float(scores[int(np.argsort(-scores)[1])]) if len(order) > 1 else 0.0)
            rows.append({
                "topic": topic.name,
                "query": query,
                "rank": rank,
                "top1": names.get(top1_id, str(top1_id)),
                "own_score": round(own, 4),
                "margin": round(margin, 4),
            })
            if rank != 1:
                failures += 1
        summary.append({
            "topic": topic.name,
            "queries": len(queries),
            "failures": failures,
            "passes": failures == 0,
        })
    return {"rows": rows, "summary": summary}


def out_of_domain_scores(queries):
    """Max blended score for queries that should match nothing."""
    from .models import Topic

    names = dict(Topic.objects.values_list("id", "name"))
    out = []
    for query in queries:
        scores, topic_ids = _blended_rank(query)
        top = int(np.argmax(scores))
        out.append({
            "query": query,
            "max_similarity": round(float(scores[top]), 4),
            "nearest_topic": names.get(topic_ids[top], str(topic_ids[top])),
        })
    return out


def probe_candidate_topics(candidates):
    """Pre-apply guard core: score CANDIDATE content against the EXISTING index.

    candidates: [{"name", "description", "example_queries"}] from an
    incoming package. For each candidate example query, report the current
    index's top topic and score: a strong capture by an unrelated topic
    means the candidate will fight existing content, and a candidate whose
    queries all land on one existing topic probably duplicates it.
    Runs BEFORE apply, so the index does not contain the candidate and
    self-retrieval tautology does not apply.
    """
    from .models import Topic

    names = dict(Topic.objects.values_list("id", "name"))
    report = []
    for cand in candidates:
        queries = list(cand.get("example_queries") or [])
        if not queries:
            queries = parse_example_queries(cand.get("description", ""))
        rows = []
        for query in queries:
            scores, topic_ids = _blended_rank(query)
            order = np.argsort(-scores)[:2]
            rows.append({
                "query": query,
                "current_top1": names.get(topic_ids[int(order[0])], "?"),
                "current_score": round(float(scores[int(order[0])]), 4),
                "current_top2": names.get(topic_ids[int(order[1])], "?") if len(order) > 1 else "",
            })
        report.append({"candidate": cand.get("name", "?"), "queries": rows})
    return report


def cross_capture():
    """Which topic's example queries are captured by a DIFFERENT topic,
    under production scoring? Post-apply complement to
    probe_candidate_topics."""
    result = self_retrieval()
    captures = {}
    for row in result["rows"]:
        if row["rank"] != 1:
            captures.setdefault(row["top1"], []).append({
                "owner": row["topic"],
                "query": row["query"],
                "rank": row["rank"],
                "margin": row["margin"],
            })
    return captures
