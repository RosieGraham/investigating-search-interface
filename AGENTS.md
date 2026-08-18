# Operating this system as an AI session

Written 10 July 2026 for whichever model works on this repository next, on the assumption that it is cheaper and less patient than the session that wrote it. Everything here was verified on that date. When this file and reality disagree, reality wins; update this file in the same commit.

## What this is

A Django service (Render, free tier, Frankfurt) plus a Chrome extension. The extension POSTs `/data/prompt/get/` with `user_search_query` and workshop identity headers. The backend embeds the query (ONNX MiniLM encoder, int8, pinned revision), scores it against a multi-vector topic index (one vector per topic description, one per example query, blended `alpha * desc + (1 - alpha) * max(example)`), and serves one approved prompt from the single global top topic above threshold. Threshold, margin and alpha live in the `Setting` table, cached 60 seconds, editable on the Content tools page with an audit trail. `RESEARCH_WRITES_ENABLED` defaults false.

A research participant may be using the live service at any time. Deploys to `main` go live automatically. Do not point Render at `release/season-2026`.

## The four commands that answer most questions

Run from `django/` with the environment described below.

- `python manage.py evaluate_matching` scores the labelled set offline and persists a provenance-stamped `MatchingEvaluation` row. Use `--file ../data/paraphrase-probe-2026-07-10.csv` for the uncontaminated probe. On this branch the harness serves one prompt from the global top topic, or silence: it does not fall through. Historical matching-quality tables that assumed fall-through are not comparable. Every quality claim starts here.
- `python manage.py audit_synthetic_marker` prints integer counts only for fields that could persist a synthetic marker. Never lists rows. Use a disposable sqlite database, not production.
- `python manage.py matching_diagnostics` prints attractor pairs, self-retrieval and out-of-domain scores.
- `python scripts/decision_grid.py` sweeps threshold and margin on both instruments.
- `python manage.py test researchdata` runs the suite. Gate 4 server, baseline, client scan, package, marker audit and frozen table: 164 tests OK on Python 3.12.7 (copy the repo to `/tmp` first). Nothing ships red.
- `python scripts/package_workshop.py` copies the extension allowlist into a clean directory, inspects it, and writes `investigating-search-interface-season-2026-v2.2.0.zip` plus a sidecar provenance file. It does not zip `web_extension_chrome/` in place. The working tree still fails inspection because of extras (README, tests, extra icons, `local_settings.example.js`). Topic exclusions are not a prohibited path. Store item ID stays pending (Rosie, Phase 5).
- `python -m flake8 .` from the repo root must exit clean before any handoff: GitHub runs it on every pull request (`.github/workflows/ci-flake8.yml`), and a red check on Rosie's screen costs a round trip. Config in `.flake8` (max line 199).
- `python manage.py audit_accounts` prints every account with its role. Read-only; run it before and after touching anything account-shaped.

## Live read-only endpoints (no credentials)

- `/healthz` liveness. Not readiness.
- `/data/release/ready/` query-free workshop readiness (`ready`, `failures`, policy hash). Does not run canaries.
- `/data/ops/status/` staff-only operational truth: git commit, model artifact hash, runtime, index fingerprint and dirtiness, content counts, last apply, last evaluation, config flags, and three canary queries with pass flags. Anonymous polling is refused so canaries cannot compete with workshop traffic.
- `/data/classifier/debug/` is disabled on this workshop branch (403, no query echo).

## Environment for local work

Copy the repo to local disk first (`/tmp`); the mounted workspace blocks SQLite and git locking (observations 42 and 51).

```bash
export DEBUG=true
export DATABASE_URL=sqlite:////tmp/isi.sqlite3
export EMBEDDING_MODEL_DIR=/tmp/model_dir   # model.onnx + tokenizer.json
# Matching identity is windowed 2026-08-20 to 2026-09-18. Before 20 Aug, freeze the clock:
# export ISI_RELEASE_CLOCK=2026-09-01T12:00:00+00:00
python manage.py download_model             # fetches the pinned int8 artifact
python manage.py migrate
python manage.py import_live_export         # 193 topics, 195 prompts, triggers
# then apply data/content-package-*.json via researchdata.services.content_apply
```

Django tests freeze that clock automatically. Production never sets `ISI_RELEASE_CLOCK`. Render must install `requirements-release.lock` with `--require-hashes` after `scripts/verify_release_lock.py`.

To mirror live content exactly, know this history: live carries the content package PLUS 17 inherited-topic descriptions applied in June from `data/topic-descriptions-draft.json` (the first batch). The 23-topic `topic-descriptions-batch-digital-2026-06-21.json` was never applied. The workshop debug URL is disabled; staff-only `/data/ops/status/` remains the canary path.

## Account roles (July 2026)

Two tiers, enforced in code, not by convention. `account/User.save()` no
longer forces privileges (it used to make every account a superuser); it
still rewrites username to the lowercased email, which is load-bearing for
login. Roles:

- **Superuser** (Rosie): everything, including participant data
  (EngagementEvent, Response, NotRelevantReport), the Setting table, user
  management, and the write actions on Content tools (upload, apply,
  rebuild, threshold).
- **Staff editor** (`is_staff=True`, `is_superuser=False`; Emilia,
  collaborators, a SEASON demo login): dashboard login; topics, prompts,
  groups, triggers and the ContentApply / MatchingEvaluation histories
  read-only; spot check and evaluation on Content tools. Cannot write
  content, cannot change the decision rule, cannot see participant data or
  other accounts. Enforcement lives in `researchdata/admin.py` (permission
  mixins), `account/admin.py`, and the per-action gate in
  `researchdata/content_tools_views.py` (`SUPERUSER_ACTIONS`).

The ethics application's data-access section cites this boundary. Loosening
any of it is an ethics change, not a UI tweak. `manage.py audit_accounts`
shows who currently holds what.

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
