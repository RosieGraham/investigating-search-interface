# Matching quality: a research work package

*9 July 2026. This is not a ticket. It is a programme of work with measurements, ablations and open questions, for the semantic matching layer of the Investigating Search Interface. Several sections deliberately do not tell you the answer. Find it, show your working, and argue for a recommendation.*

## How to work

Phased, with gates. **Phase 1 is a gate: nothing after it may be merged until the harness exists and a baseline is committed.** Every claim after that must be backed by a table produced by the harness. Assertions without tables will be treated as unreviewed. Where this brief poses an open question, answer it with an experiment, not an opinion. Where you disagree with a design here, say so and show the numbers that justify the disagreement. I would rather be corrected than obeyed.

Work on a branch. `main` deploys automatically to a live service that a research participant is using this week.

---

## 1. The system, and what we measured today

A Django app injects a reflection prompt above Google's results via a Chrome extension. For each search, the backend embeds the query with `sentence-transformers/multi-qa-MiniLM-L6-cos-v1` (ONNX, 384 dims), compares it by cosine similarity against a vector per topic, takes topics above a threshold, and serves up to four prompts from the best-matching topic.

A legacy substring fallback (`_trigger_match`) was switched off in production today via `TRIGGER_FALLBACK_ENABLED=false`. It split the query on spaces and ran `trigger_text__icontains=word`, so `pizza near me` matched on "me" inside "memory" and served four prompts about politics. Read it before you touch anything; it is a museum piece and a warning.

Measured against the live service, threshold 0.35, fallback off, on the 51-query spot-check set:

| Metric | Value |
|---|---|
| served by classifier | 38 |
| served by fallback | 0 |
| silent | 13 (5 negative controls, 8 recall gaps) |
| negative controls firing a card | 0 of 5 |

The eight recall gaps: `how much does Google pay Apple`, `why are product review sites all the same`, `lateral reading`, `how to remove my name from Google`, `why does the algorithm show me this`, `why is reject all so hard to find`, `are vaccines safe`, `best multivitamin reddit`.

Two confident, wrong answers:

| Query | Top-1 | Top-2 | Correct |
|---|---|---|---|
| why does everyone use Google | Children and search, 0.4350 | The Google effect on memory, 0.4343 | Concentration and defaults |
| how do I know if a website is reliable | Wikipedia and how online knowledge is made, 0.4724 | Fact-checking, 0.3823 | Search literacy |

Note the first: the top two are separated by 0.0007. That is a coin toss presented as an answer.

`are vaccines safe` is a different animal. It classifies correctly to Vaccination at 0.6012, but that topic has no approved prompts, so nothing is served. That is a content gap, not a matching gap. Do not "fix" it. The harness must score it as correct that no card appeared.

## 2. Two defects in how a topic becomes a vector

**We embed the contrasts.** `Topic.embedding_text` returns the entire `description`. Our descriptions end with "Distinct from X (...), from Y (...)". That sentence exists so a human editor keeps topics apart, and it does the opposite to the vector: it injects each competitor's vocabulary into this topic's own embedding. "Children and search" literally contains the phrase "how search works for adult users", and its example queries include "should children use Google". Hence `why does everyone use Google` landing on it.

**We mean-pool one long blob.** A description is roughly 120 words in one vector. `why does everyone use Google` appears *verbatim* inside Concentration and defaults' description and still loses, because one clause barely moves the average. Likewise `how do I know if a website is reliable`, verbatim inside Search literacy.

## 3. Ground truth

`data/labelled-queries-2026-07-09.csv`, columns `query, expected_topic, expected_card, category`.

- 51 rows. 6 rows have `expected_topic=NONE`: five tagged `negative-control`, plus `best multivitamin reddit`, which is `ordinary-uncovered` (no topic exists for it).
- 7 rows have `expected_card=no`: those six plus `are vaccines safe`.
- `Vaccination` is an expected topic that is **not** in the content package, because it is an inherited topic. Your harness must handle expected topics outside the package without crashing.

This set is small, mine, and biased toward the collisions I was worried about. Treat it as a seed, not scripture. **Say so in your report, and say what a better set would contain.** A larger one, built from real searches by a human evaluator across ten query shapes (ordinary, transactional, navigational, messy, sensitive, negative controls and so on), arrives in about two weeks. Design the schema so it can absorb that without a rewrite.

---

## Phase 1 (gate). The evaluation harness

`python django/manage.py evaluate_matching --file <csv> [--csv out.csv] [--json out.json]`, offline, against the local database, no HTTP.

Report:

- **accuracy@1** over `expected_card=yes` rows: top matched topic equals `expected_topic`
- **coverage**: share of `expected_card=yes` rows that clear the decision rule
- **false positive rate**: share of `expected_topic=NONE` rows returning any card. Report separately for the five `negative-control` rows and for `ordinary-uncovered`.
- **abstention rate**
- **confusion list**: every wrong row with expected, actual, top-1, top-2, margin
- **per-category** and, once the field exists, **per-query-shape** breakdowns
- **mean reciprocal rank** of the expected topic, so improvements that move the right answer from rank 5 to rank 2 are visible even before they reach rank 1

Persist every run to a `MatchingEvaluation` model (timestamp, git sha, model id, threshold, margin, index fingerprint, the metrics, the full per-row results as JSON). We want a time series, not a moment. This becomes the methods evidence for a paper and a funding bid, so provenance matters more than prettiness.

Expose it as a read-only panel on the existing Content tools page, runnable in the browser against the live database.

**Commit the baseline before changing anything else.** Everything below is judged against it.

Add `!data/labelled-queries-*.csv` to the `.gitignore` exception list and commit the CSV.

## Phase 2. Diagnostics, before treatment

Build these because they explain, and because they will keep earning their keep.

**Attractor detector.** For every pair of topics, compute the cosine similarity of their representations. Report the top N most-confusable pairs. This is the tool that would have caught Accessibility of search and Children and search swallowing seven unrelated topics before we shipped them. Run it as part of the harness and as a pre-apply check in the content pipeline.

**Self-retrieval.** For each topic, embed its own example queries and ask: does the topic retrieve them at rank 1? A topic that cannot find its own seed queries is broken. Report a per-topic table with rank and margin. I predict Concentration and defaults and Search literacy fail this today. Confirm or refute.

**Out-of-domain scores.** For each negative control, report `max` similarity across all topics. If genuinely-irrelevant queries sit at 0.24 and genuine ones at 0.50, an abstention rule is easy; if they overlap, say so, because that changes everything downstream.

## Phase 3. Representation

Split `Topic` into `description` (clean prose only), `example_queries` (list), and `contrasts` (free text, **never embedded**, editorial only). Write a reversible data migration parsing the existing single-blob descriptions, which all share the shape *prose, then "Example queries: a, b, c.", then "Distinct from ..."*. Assert that recombining the three fields reproduces the original string for every row and fail loudly if it does not.

Then index each topic as **several vectors**: the clean description, plus one per example query.

The baseline scoring proposal is:

```
score(topic, q) = max( cos(q, v_desc), max_i cos(q, v_example_i) )
```

**Open question 1.** Is max the right pooling? Compare against: mean; description-only; example-queries-only; a weighted blend `a * desc + (1-a) * max_example` swept over `a`; and a late-interaction style sum of top-k example similarities. Give the harness table for each. Recommend one and defend it.

**Open question 2.** Ablate the contrasts. Quantify exactly how much removing "Distinct from ..." from the embedded text changes accuracy@1 and the two known bad cases. If it changes nothing, say so; my hypothesis may be wrong and I would like to know.

### Measured evidence for this phase, taken from the live service on 9 July

Self-retrieval already fails badly. These are seed queries taken verbatim from a topic's own description:

| Seed query, and the topic that owns it | Rank 1 | Rank 2 | Owner's rank |
|---|---|---|---|
| `why does everyone use Google` (Concentration and defaults) | Children and search, 0.4350 | The Google effect on memory, 0.4343 | not in top 3 |
| `how much of search does Google control` (Concentration and defaults) | Children and search, 0.4920 | The Google effect on memory, 0.4822 | not in top 3 |
| `how do I know if a website is reliable` (Search literacy) | Wikipedia, 0.4724 | Fact-checking, 0.3823 | not in top 3 |
| `teaching search literacy` (Search literacy) | Search literacy, 0.6743 | AI and Search Engines, 0.4700 | rank 1 |

So a topic can fail to retrieve two of its own five seed queries, and not even place. Short distinctive seeds (`teaching search literacy`) succeed; long natural-language seeds lose to whichever topic happens to own a keyword (`reliable` belongs to Wikipedia).

The likely cause of the Children and search captures is a single example query added on 9 July: **"parental controls on search"**. The token "controls" appears to be pulling `how much of search does Google control`.

**This is a warning about Phase 3's design, not just a bug.** Max-pooling over example-query vectors would make one badly chosen example query *more* powerful, not less. A single careless seed could capture a whole neighbourhood of unrelated traffic, and nothing in the current pipeline would catch it.

So: **treat the Phase 2 diagnostics as a hard prerequisite for the Phase 3 scoring change, not an accompaniment.** Before max-pooling ships, the attractor detector and the self-retrieval check must run in the content pipeline, and a topic whose example query out-retrieves a *different* topic's seed queries must be flagged. Consider also scoring example queries with a discount relative to the description, or capping any single example query's contribution. Test it. Report which of these actually prevents the "parental controls" failure.

**Open question 3.** The inherited topics. 193 topics carry no description and embed on `"{group}: {name}"` alone. `are vaccines safe` reaches Vaccination at 0.6012 that way, so name-only embedding clearly works. But these topics also compete: `who regulates Google` used to land on "Government control of the internet". Measure whether excluding undescribed topics from the index improves or harms the labelled set, and whether they should instead be given a cheap synthetic description. This decides whether the long-planned "write 193 descriptions" project is worth doing at all, so answer it carefully.

Three traps:

- `_topics_fingerprint()` hashes `embedding_text`. It must hash the description **and** the example queries, or a stale on-disk index gets reused after an edit and nothing appears to change.
- The `post_save` signal on `Topic` must still mark the index dirty when only `example_queries` changes.
- Keep `encode()` chunked at `batch_size=16`. The extra vectors are cheap (roughly 35 topics times 6 queries, about 210 rows of 384 floats, well under a megabyte). Do not load the ONNX model at import time.

## Phase 4. The decision rule

Today: `top1 >= threshold`. Replace with `top1 >= threshold` **and** `top1 - top2 >= margin`, else abstain. Store both in the existing `Setting` model, read through the cached `classifier_config` getters, expose both on the Content tools Status panel. Default margin 0, so behaviour is unchanged until tuned.

Sweep a grid of thresholds and margins. Report accuracy@1, coverage and false-positive rate at each. Choose the point that holds false positives at zero and maximises coverage. Show the grid.

**Open question 4.** Is a fixed global threshold defensible at all? Consider per-topic thresholds, or a normalised score (for example, `top1` minus the mean similarity across all topics, which is a cheap out-of-domain signal). Argue from the data.

## Phase 5. Honest confidence

The extension shows the user a match percentage, and our evaluator records it. That number is currently a raw cosine similarity. Cosine similarity is not a probability. Showing 0.4044 as "40% match" to a reader is, on this project of all projects, an uncomfortable thing to do without thinking about it.

**Open question 5.** Fit a calibration map from cosine similarity to something honest, using the labelled set: a reliability diagram, then Platt scaling or isotonic regression. Report calibration error before and after. Then recommend what the card should actually display, if anything. A defensible answer may be "show nothing, or show a coarse band". This is a research question about interface honesty, not just a maths one, and the project's own argument is that machine confidence is not correctness.

## Phase 5b. Quantisation, and whether our decisions are made of noise

`download_model.py`'s `ONNX_CANDIDATES` list is ordered `onnx/model_qint8_avx512_vnni.onnx`, `onnx/model_quint8_avx2.onnx`, `onnx/model_qint8_arm64.onnx`, `onnx/model_O2.onnx`, and takes the first that downloads. So production is almost certainly serving an **int8 quantised** encoder.

Loading the fp32 `onnx/model.onnx` offline on 9 July reproduced none of the live scores: every value came out 0.02 to 0.05 too high, and two top-1 rankings flipped, including `why does everyone use Google`.

Now hold that against the fact that the top two topics for that query are separated by **0.0007**.

**Open question 5b.** Is quantisation error the same size as, or larger than, the margins on which this tool is currently making decisions? Measure it: embed the whole topic corpus and the labelled query set under fp32, under `model_O2`, and under each quantised artifact, and report the distribution of score deltas and the number of rank flips in the top three. If quantisation noise routinely exceeds the top-1 to top-2 margin, then several of our "confident wrong answers" are not semantic failures at all, and the remedies differ completely: a margin rule and a higher-precision topic index, rather than rewritten descriptions.

Then decide what production should run. There is a real trade: fp32 is 90MB and slower, and the instance has 512MB with one worker. Quantifying that trade is part of this phase. Also confirm which artifact Render actually downloaded on its last build, from the deploy logs, rather than inferring it from the candidate list.

Any offline replica must load the same artifact as production and replicate the tokenizer exactly (`enable_truncation(max_length=256)`, then `enable_padding()`, mean pooling over the attention mask, L2 normalisation). Before trusting any offline number, reproduce these live readings to within 0.002: `what is SEO` to Search engine optimisation at 0.5110; `why does everyone use Google` to Children and search at 0.4350; `how do I know if a website is reliable` to Wikipedia at 0.4724; `weather tomorrow` to U.S. Election 2024 at 0.2425.

## Phase 6. Model and efficiency (optional, only if earlier phases land)

**Open question 6.** Would a different encoder do better inside 512MB with the ONNX runtime and one worker? Benchmark at least two credible alternatives (for example `all-MiniLM-L12-v2`, `bge-small-en-v1.5`, `gte-small`) on the labelled set, reporting accuracy@1, index build time, peak memory and per-query latency. Note that `multi-qa-MiniLM-L6-cos-v1` is trained for question-to-passage asymmetric retrieval, while much of what we now do is question-to-question. That mismatch may matter. Also consider int8 quantisation and an LRU cache of query embeddings.

**Open question 7.** The project's funding case claims multilingual potential through semantic embeddings. Our current model is English-only. Run a handful of French, Spanish and Portuguese queries against it and report honestly what happens. If the claim is not currently supportable, say so plainly; it is far better to know now than in a reviewer's report. If a multilingual model would support it within the memory budget, say what it would cost.

## Phase 7. Related threads you may fold in, with care

- **Prompt display order.** `Working Docs/Priority fix - prompt display order - 9 July 2026.md` in the parent workspace describes a six-line change: similarity selects *which* prompts appear, editorial priority decides the *order*. It is a separate concern from topic matching. If you take it, put it behind its own commit and its own test, and do not entangle it with anything above.
- **Pre-apply guard.** The content pipeline should refuse to ship a topic whose nearest neighbour exceeds some similarity, or whose own example queries do not self-retrieve. Wire the Phase 2 diagnostics into the Content tools upload preview as a warning.
- **`scripts/calibrate_threshold.py`** predates all of this. Either fold it into the harness or delete it. Do not leave two ways to answer the same question.

---

## Open questions, collected

1. Which pooling over description and example queries, and why?
2. How much do embedded contrasts actually hurt?
3. Should undescribed inherited topics be indexed, excluded, or synthesised?
4. Is a global threshold defensible, or should it be per-topic or normalised?
5. What, if anything, should the card honestly display as confidence?
6. Would a different encoder, or quantisation, pay for itself inside 512MB?
7. Is the multilingual claim supportable today?
8. What should a good labelled set contain, and how large must it be before these numbers mean anything? Give a power-analysis-flavoured answer, however rough.
9. Anything I have not thought of. The section I most want to read is the one I did not ask for.

## Constraints

- Render free tier: one gunicorn worker, four threads, 512MB, `--timeout 120`, no shell.
- The public API response shape must not change: `prompt`, `prompts`, `topics`, `classifier`, and per-prompt `matched_by` and `confidence`. A Chrome extension in active use depends on it.
- Do not change prompt text. Do not approve or unapprove anything. Do not delete.
- Leave `TRIGGER_FALLBACK_ENABLED=false`. If you rewrite `_trigger_match`, it must match whole words of at least four characters against a stop list, never `icontains` on a bare word, and it stays off by default.
- Nothing that touches participant data (`EngagementEvent`, `Response`). Do not read it, do not export it, do not include it in any report.
- Stay on a branch. Run tests from `/tmp`, not the mounted workspace folder.
- Do not read or write anything outside this repository.

## Deliverables

1. The harness, the diagnostics, the migration, and the scoring change, each in its own commit.
2. `docs/matching-quality-report.md`: baseline table, every ablation table, the tuning grid, the calibration analysis, the answers to the open questions, and a numbered list of recommendations ordered by expected gain per unit of risk.
3. A short section titled **What I could not verify**, and what would be needed. Real Render memory, real Neon latency and real ONNX timings on the free tier cannot be measured from a laptop. Say so rather than guessing.

## Acceptance

Judged only against the committed baseline, on the harness:

1. False positives on the five negative controls: **0 before, 0 after. Hard constraint, not a target.**
2. `are vaccines safe` still returns no card, scored correct.
3. `why does everyone use Google` resolves to Concentration and defaults, or abstains. Landing on Children and search is a failure.
4. `how do I know if a website is reliable` resolves to Search literacy, or abstains.
5. accuracy@1 and coverage both improve. If one improves at the other's cost, show the trade and defend the choice.
6. At least four of the eight named recall gaps close.
7. Every open question above is answered with a table, or explicitly declined with a reason.
