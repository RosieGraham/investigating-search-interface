# Architecture and decisions

June 2026 rebuild. For the step-by-step setup see DEPLOY.md.

## The autonomy decision

The project moved off University of Birmingham infrastructure (where every
change required a Research Software Engineer) onto:

- **Render** (free tier) - hosts the Django app; deploys automatically on
  every git push
- **Neon** (free tier) - persistent managed Postgres
- **GitHub** (Rosie's account) - source of truth

Option A (refactor + rehost) was chosen over a serverless rebuild (B) and a
fully client-side extension (C) because:

1. The Django admin is the content-editing surface for a non-developer;
   A keeps it for free, B and C replace it with custom work.
2. Three months to the September 2026 conference: A reuses ~80% of working
   code and concentrates new effort on the two things that matter (the
   classifier and the content script).
3. C removes the centralised feedback channel (NotRelevantReport,
   EngagementEvent) - the "research instrument" claim in funding bids.
4. Empirically verified fit: ONNX int8 model runs in ~120MB RSS, 11ms per
   query - comfortable inside Render's free 512MB / 0.1 vCPU instance.

Phase 2 option (post-September): move classification client-side
(transformers.js) for a privacy-positive story - queries never leave the
browser - while the server keeps serving content and collecting feedback.
The narrow `classify_query` interface also allows a remote classifier
(e.g. a PeARS API, per Aurelie Herbelot's collaboration offer) without
touching the views.

## Query matching

Primary: SBERT vector classification (`researchdata/embedding.py`).
`multi-qa-MiniLM-L6-cos-v1`, int8 ONNX (~22MB), mean pooling, cosine
similarity against per-topic description embeddings, threshold + top-k from
settings. Academic basis: WC-SBERT (Chi & Jang 2024, ACM TIST
10.1145/3678183); design lineage Top2Vec (Angelov 2020).

Fallback: the legacy trigger substring matching (bugs fixed), used when the
classifier is disabled, unavailable, or finds nothing above threshold. The
10,306 imported triggers also serve as calibration positives.

Topic descriptions are the editorial lever: `Topic.description` (admin
field) is what gets embedded; topics without one fall back to
"group: name". First 17 drafted in `data/topic-descriptions-draft.json`;
measurable effect documented in the decision document.

Threshold: **0.40** at launch (calibrated 2026-06-11 against 500 trigger
positives + 50 constructed negatives: 79.6% coverage, 14% FP). Re-calibrate
after each description batch, and switch the negative set to real
NotRelevantReport rows once they accumulate.

## API

`GET /data/prompt/get/` - query in, up to 3 prompts out, with per-prompt
`matched_by` ('classifier'|'trigger'), `confidence`, `seeed_url`. Backward
compatible with the v1 popup (`prompt` single object preserved).
`POST /data/response/post/`, `/data/notrelevantreport/post/` - as v1, the
report now carries `classifier_confidence`.
`POST /data/event/post/` - anonymous engagement events (see below).
`GET /healthz` - health check / keep-alive target.

## Research instrumentation

- **NotRelevantReport**: labelled query/topic mismatches (was empty in the
  live system at migration - the table now also records the classifier's
  confidence at report time, making it directly usable for calibration).
- **EngagementEvent**: anonymous session-level events (shown, expanded,
  dismissed, SEEED click, response, report) with a random per-installation
  key, never the query text. Users can opt out in the popup. Supports
  session-level engagement analysis and post-intervention designs (the
  9%-of-studies gap named in the funding context).

## Extension (Manifest V3)

Content script + service worker. All API calls go through the worker
(host_permissions authorise them; no CORS exposure on the page). Three
layouts detected live (June 2026): classic (`#rso`, no AI block), hybrid
(AI block + `#rso` - inject before `#rso`), AI Mode (`udm=50`, no `#rso` -
floating badge with expandable panel; full inline support is Phase 2).
MutationObserver + Navigation API/polling handle dynamic rendering and
SPA-style updates. All selectors are in one `SELECTORS` object.

## SEEED connection

`Prompt.seeed_url` exists now so no migration is needed when SEEED goes
live; cards render a "Learn more in SEEED" link when set, and clicks are
logged as engagement events.

## Known gaps / future work

- No climate-change topic exists in the imported data; "climate crisis"
  queries currently best-match the HAARP conspiracy topic (0.45). Add a
  proper climate topic (or sharpen the HAARP description's contrast) before
  the conference demo.
- 176 topics still need descriptions (drafting workflow:
  `data/topic-descriptions-draft.json` + `scripts/apply_topic_descriptions.py`).
- Most prompts are placeholder stubs and only 6 are approved - editorial
  sessions needed.
- AI Mode inline injection (Phase 2) once its DOM stabilises.
- Multilingual: swap `EMBEDDING_MODEL_ID` to
  `paraphrase-multilingual-MiniLM-L12-v2` (env var change + re-index) -
  named as a future direction in European bids.
