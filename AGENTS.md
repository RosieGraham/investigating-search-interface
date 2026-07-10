# Operating this system as an AI session

Written 10 July 2026 for whichever model works on this repository next, on the assumption that it is cheaper and less patient than the session that wrote it. Everything here was verified on that date. When this file and reality disagree, reality wins; update this file in the same commit.

## What this is

A Django service (Render, free tier, Frankfurt) plus a Chrome extension. The extension asks `/data/prompt/get/?user_search_query=...` what to inject above Google's results. The backend embeds the query (ONNX MiniLM encoder, int8), scores it against a multi-vector topic index (one vector per topic description, one per example query, blended `alpha * desc + (1 - alpha) * max(example)`), and serves approved prompts from the best topic above threshold. Threshold, margin and alpha live in the `Setting` table, cached 60 seconds, editable on the Content tools page with an audit trail.

A research participant may be using the live service at any time. Deploys to `main` go live automatically.

## The four commands that answer most questions

Run from `django/` with the environment described below.

- `python manage.py evaluate_matching` scores the labelled set offline and persists a provenance-stamped `MatchingEvaluation` row. Use `--file ../data/paraphrase-probe-2026-07-10.csv` for the uncontaminated probe. Every quality claim starts here.
- `python manage.py matching_diagnostics` prints attractor pairs, self-retrieval and out-of-domain scores.
- `python scripts/decision_grid.py` sweeps threshold and margin on both instruments.
- `python manage.py test researchdata` runs the suite (63 tests, all passing as of this commit). Nothing ships red.

## Live read-only endpoints (no credentials)

- `/healthz` liveness.
- `/data/ops/status/` machine-readable operational truth: git commit, model artifact hash, runtime, index fingerprint and dirtiness, content counts, last apply, last evaluation, config flags, and three canary queries with pass flags. **Check this first when anything looks wrong. It exists because this project's expensive failures have all been invisible state.**
- `/data/classifier/debug/?user_search_query=...` top-5 topics with confidences, the top1-top2 gap, and whether the production margin would abstain. Stores nothing.

## Environment for local work

Copy the repo to local disk first (`/tmp`); the mounted workspace blocks SQLite and git locking (observations 42 and 51).

```bash
export DEBUG=true
export DATABASE_URL=sqlite:////tmp/isi.sqlite3
export EMBEDDING_MODEL_DIR=/tmp/model_dir   # model.onnx + tokenizer.json
python manage.py download_model             # fetches the int8 artifact
python manage.py migrate
python manage.py import_live_export         # 193 topics, 195 prompts, triggers
# then apply data/content-package-*.json via researchdata.services.content_apply
```

To mirror live content exactly, know this history: live carries the content package PLUS 17 inherited-topic descriptions applied in June from `data/topic-descriptions-draft.json` (the first batch). The 23-topic `topic-descriptions-batch-digital-2026-06-21.json` was never applied. When in doubt, probe `/data/classifier/debug/` per topic name and trust `has_description` over any document, including this one.

## Rules that are not negotiable

- Never push to `main`. Work on a branch; Rosie merges through GitHub Desktop.
- Never read, export or report `EngagementEvent` or `Response` rows. The ethics application covering them is not yet submitted.
- Never accept a pasted secret (database URL, API key, token) into a transcript. The mechanism for credentials is an MCP connector with the secret in its own settings, or Rosie acting in her own browser.
- Do not change prompt text, approve or unapprove content, or delete anything as a side effect. Content changes go through the Content tools upload flow with its dry run, digest check, guard warnings and audit row.
- The public API response shape (`prompt`, `prompts`, `topics`, `classifier`, per-prompt `matched_by` and `confidence`) is load-bearing for the deployed extension. Add fields; never rename or remove.
- `TRIGGER_FALLBACK_ENABLED` stays false. The substring fallback is a museum piece.

## Numbers you must not over-read

Scores from the int8 encoder carry noise: sd about 0.0115 versus fp32, and cross-architecture disagreement of similar size. Any two scores within about 0.02 of each other are the same score. One decision in five sits inside that band (measured 10 July 2026). Do not rewrite content to fix a sub-0.02 ranking; it is weather, not climate. `MatchingEvaluation` rows record artifact hash and runtime so you only compare like with like.

The 2026-07-09 labelled set is contaminated for representation work: 43 of its 44 positive rows are verbatim topic example queries. Use `data/paraphrase-probe-2026-07-10.csv` for honest evaluation until the successor labelled set exists. Zero false positives on 5 or 10 negative controls is weak evidence; treat FP claims as bounded, not proven.

## Where things are

- `docs/matching-quality-report.md` answers the nine open questions with tables; `docs/evaluations/` holds every table as JSON.
- `MATCHING-QUALITY-BRIEF.md` is the programme this all responds to.
- `researchdata/evaluation.py` is the harness core (pluggable rankers); `researchdata/diagnostics.py` the instruments; `researchdata/description_blob.py` the blob splitter.
- The parent workspace folder `Investigating Search Interface/Working Docs/` holds session handoffs and editorial state; `skill-observations/log.md` two levels up holds the observation log this project's sessions maintain.

## When you finish a session

Log observations to the workspace observation log as they occur, not at the end. Update `OPEN-THREADS.md` in the workspace root. Leave the branch clean (tests green, no uncommitted noise), and write one paragraph in your handoff saying what a cold-start session must know that this file does not already say. If that paragraph contains a fact this file should carry, add it here instead and say so.
