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
- `python manage.py test researchdata` runs the suite. Gate 4 server, baseline, client scan, package, marker audit, frozen table and public pages: 179 tests OK on Python 3.12.7 (copy the repo to `/tmp` first; interpreter `/tmp/isi-season-venv`). Nothing ships red. JS contract tests: 15 of 15 (`node --test web_extension_chrome/test/*.mjs`).
- `python scripts/package_workshop.py` copies the extension allowlist into a clean directory, inspects it, and writes `investigating-search-interface-season-2026-v0.2.1.zip` plus a sidecar provenance file. It does not zip `web_extension_chrome/` in place. The working tree still fails inspection because of extras (README, tests, extra icons, `local_settings.example.js`). Topic exclusions are not a prohibited path. Store item ID stays pending (Rosie, Phase 5). Rosie confirmed on 21 August that this developer account has never uploaded an item, so `0.2.1` is a legal first version.
- `python -m flake8 .` from the repo root must exit clean before any handoff: GitHub runs it on every pull request (`.github/workflows/ci-flake8.yml`), and a red check on Rosie's screen costs a round trip. Config in `.flake8` (max line 199).
- `python manage.py audit_accounts` prints every account with its role. Read-only; run it before and after touching anything account-shaped.

## Live read-only endpoints (no credentials)

- `/healthz` liveness. Not readiness.
- `/data/release/ready/` query-free workshop readiness (`ready`, `failures`, policy hash). Does not run canaries.
- `/data/ops/status/` staff-only operational truth: git commit, model artifact hash, runtime, index fingerprint and dirtiness, content counts, last apply, last evaluation, config flags, and three canary queries with pass flags. Anonymous polling is refused so canaries cannot compete with workshop traffic.
- `/data/classifier/debug/` is disabled on this workshop branch (403, no query echo).
- `/` is the public landing for Store Homepage and Support. Anonymous 200, no database, no third-party scripts or fonts, does not extend `base.html`. Copy is section 5a of the 19 August signed disclosure set, amended and approved the same evening. It still has to be live on Render before upload, because reviewers hit production.
- `/privacy/` is the workshop notice (anonymous 200, no database, no third-party scripts or fonts). It must not extend `base.html`, which loads Google Fonts. Copy is the 19 August signed set. Contact is `R.Graham@bham.ac.uk`. The version line still reads `Version 1.0, [deploy date]` and is filled with the calendar day the page goes live, not before. Named processors stay generic.
- `/cookies/` is the same class of page as `/privacy/` (standalone HTML, no `base.html`, no `cookiesmsg.js`). Live `main` still extends `base.html`, loads Google Fonts, and shows a banner that writes `cookieMessageApprove` if accepted. Do not deploy the signed cookies copy while that chrome is still attached.
- Consent banner in `content.js` is the signed 19 August paragraph. `NOTICE_VERSION` is `season-2026-v2`. `lib/request_lifecycle.js` will only acknowledge that exact key: a leftover `season-2026-v1` check would silently disable matching after consent.
- Listing `name` is `Investigating Search Interface`. Machine `version` is `0.2.1`, `version_name` is `Alpha build 0.2.1`, `BUILD_ID` is `0.2.1-season-2026-1`. Event year stays in the attribution string and the popup subtitle (`SEASON 2026 workshop`), not in the Store title.
- Do not package until freeze row 10 (card landing position, unpacked extension) is closed, and `/privacy/` plus `/` are live on Render. Reviewers hit production.

## Client freeze constants (18 August 2026)

These are packaged. Changing any of them after the first Store upload costs a full Chrome review.

- First matching attempt 12000ms, one retry after 350ms. `OBSERVER_TIMEOUT` is 26000 so the layout observer outlives two attempts (12000 + 350 + 12000 = 24350). A 20000ms observer would still expire underneath the retry.
- Content-script `matches` is an explicit list of 19 Google hosts (European ccTLDs plus `.com.au` and `.ca`), not a TLD wildcard. `www.google.de` homepage 301s to `www.google.com`, but `www.google.de/search` can remain on the country host, so the widening is load-bearing for Hamburg attendees.
- Layout selectors (`#rso`, `#search`, `[data-subtree="aimc"]`) were verified on live Google on 19 August in classic, hybrid and AI modes; none had drifted and `currentMode()` resolved correctly in each. What remains is whether the card lands in the intended position, which needs the unpacked extension on a headed profile. That still blocks the ZIP.

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
