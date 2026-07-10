# Matching quality report

10 July 2026. Produced by the session that worked MATCHING-QUALITY-BRIEF.md end to end on branch `matching-quality`. Every number in this report comes from a table committed under `docs/evaluations/`, produced by the Phase 1 harness or one of the study scripts under `scripts/`. Where a claim rests on fewer than a hundred data points, the n is stated beside it.

One caveat governs everything: these numbers were produced on an aarch64 sandbox running the int8 AVX512-VNNI artifact under onnxruntime 1.23.2. Production is x86. Identical weights on these two architectures disagree by sd 0.014 per score. Decision-level results (which topic won, whether a card was served) transfer; third decimal places do not. Section 1 explains why this is itself a finding rather than an apology.

## 1. The reproduction gate, and why it could not be passed as written

The handoff required reproducing four live readings to within 0.002 before drawing any conclusion. I could not, and the reason matters more than the gate.

What happened, in order. First, the corpus recipe in the handoff (live export plus content package equals live corpus) turned out to be incomplete: probing all 228 topic names against the live debug endpoint showed 17 inherited topics carrying descriptions applied in June from `data/topic-descriptions-draft.json`'s first batch, recorded in no handoff or tracker. The same probe confirmed the 23-topic digital description batch committed on 21 June was never applied at all. Two silent divergences between repository and live state, in opposite directions. The replica corpus was corrected against the probe: all 228 description flags now match live exactly.

Second, with corpus and pipeline verified, per-pair score deltas against live still ran sd 0.014 (max 0.06) under every one of the five candidate artifacts, including exact int8 matches for what production runs. The cause is cross-architecture int8 kernels: onnxruntime executes quantised matmuls differently on aarch64 than on x86 AVX512-VNNI, and fine-grained cosine values are properties of weights plus kernels plus architecture. A score-exact gate between different architectures is unpassable in principle, not just in practice.

The substitute gate this work proceeded under, with the previous session's intent preserved: decision-level fidelity plus a measured noise band. Against live, the replica agrees on 100 percent of above-or-below-threshold decisions across all 228 topic-name probes, and on 224 of 228 top-1 topics (98.2 percent). On the collision-heavy labelled set, agreement is 43 of 51, and every disagreement sits at a live margin below 0.033. Any conclusion in this report resting on margins under about 0.03 is flagged as inside the noise band.

Which artifact does Render actually run? The candidate list, an unchanged HuggingFace repository (no commits since November 2024), and the download-first-that-resolves logic all point to `model_qint8_avx512_vnni.onnx`. The live service's quantisation signature (scores sitting below fp32 by roughly the int8 shift, correlation 0.38 with my local int8 shift) supports int8. Definitive confirmation needs one line from the Render build log: look for "Trying onnx/model_qint8_avx512_vnni.onnx..." followed by "Model ready". I could not read the deploy logs from here.

The finding hiding in the failure: the live tool's most confident-looking wrong answer separated its top two topics by 0.0007. The measured cross-runtime disagreement is twenty times that. The same weights, run on two legitimate machines, flip that decision. Sub-noise margins are not judgements, and Section 7 follows this through the decision rule.

## 2. Baseline

`docs/evaluations/baseline-2026-07-10.json`, threshold 0.35, margin 0, replica of live behaviour on 9 July, MatchingEvaluation run 1:

| metric | value |
|---|---|
| accuracy@1 (served) | 0.659 |
| accuracy@1 (raw top-1) | 0.705 |
| coverage | 0.864 |
| false positives, 5 negative controls | 0 |
| false positives, ordinary-uncovered | 0 |
| are-vaccines-safe scored correctly silent | yes |
| abstention rate | 0.255 |
| MRR of expected topic | 0.777 |

This matches the live service on every decision in the 9 July spot-check: 38 served, 13 silent, 0 fallback. The confusion list reproduces both named wrong answers and all eight recall gaps. Two of those gaps (`lateral reading`, `how to remove my name from Google`) already ranked their expected topic first and failed only on threshold, which said early that representation, not ranking, was the problem for at least part of the set.

## 3. The labelled set cannot evaluate what Phase 3 built, and that is a headline result

43 of the 44 expected-card rows in `labelled-queries-2026-07-09.csv` are verbatim example queries of their expected topic (case and punctuation insensitive). One genuine positive (`data void search`), one uncovered probe, and the five negative controls are the only uncontaminated rows.

The consequence: any scoring scheme that indexes example queries as vectors scores a perfect 1.0 on this set by construction, because 43 of its questions are literally in the index. The set stays useful as a regression floor (the acceptance criteria are defined on it, and Section 6 reports them), but it cannot distinguish good generalisation from exact recall.

The instrument built to fix this: `data/paraphrase-probe-2026-07-10.csv`. 44 intent-preserving rephrasings of the same queries (checked programmatically: none is verbatim in any topic's example list), the vaccines and multivitamin shapes rephrased, the original five negative controls, and five fresh negative controls. All probe numbers below come from it. It is synthetic and written by one author in one sitting; it is a bridge to Emilia's real-search set, not a replacement. Section 12 gives the size a real set needs.

## 4. Diagnostics (Phase 2)

`docs/evaluations/diagnostics-2026-07-10.json`, run against the pre-change single-vector index.

Self-retrieval: 30 of 52 described topics failed to retrieve at least one of their own example queries at rank 1 (63 of 261 seeds lost). The brief predicted two failures; the truth was fifteen times worse. Search engine optimisation lost 5 of 6 seeds. Government surveillance lost 4 of 4. The two topics the brief named (Concentration and defaults, Search literacy) both fail, confirming its prediction, but they are unremarkable members of a large class.

Attractors: the most confusable described pair is Defaults and choice against Concentration and defaults at 0.778, and the labelled set expects them to be distinguished. Among undescribed topics, confusability runs far higher (AI disinformation and Unregulated AI at 0.887), which matters because of what follows.

Out-of-domain behaviour, and a load-bearing accident. Four of five negative controls sit safely at 0.20 to 0.23. But `bbc news` reaches the undescribed topic Fake news at 0.52, far above threshold. No card fires today for exactly one reason: Fake news has no approved prompts, so the serving fall-through discards it and nothing else clears threshold. The corpus is full of such chaff: undescribed, promptless topics that absorb junk queries and keep false positives at zero by accident. The day someone writes an approved prompt for Fake news, every navigational news query gets a misinformation card. The pre-apply guard (Phase 7b) now warns when new content would collide with existing attractors, but the deeper point stands: the system's headline zero-false-positive figure is partly structural luck, and Section 11's open question 3 answer depends on it.

## 5. Representation (Phase 3): what was measured and what shipped

The blob was three things fighting in one vector: prose, example queries, contrasts. They are now three fields (reversible migration, byte-exact recombination asserted per row, verified 52 of 52 before writing), and the index holds one vector per description plus one per example query, 489 rows for 228 topics.

Ablations on the paraphrase probe, threshold 0.35, `docs/evaluations/ablations-paraphrase-2026-07-10.json`:

| representation | acc@1 | coverage | FP neg (n=10) | FP unc (n=1) | MRR |
|---|---|---|---|---|---|
| baseline (full blob, single vector) | 0.568 | 0.795 | 0 | 1.0 | 0.704 |
| prose only, single vector | 0.545 | 0.841 | 0 | 1.0 | 0.631 |
| prose plus examples, contrasts stripped | 0.591 | 0.773 | 0 | 1.0 | 0.707 |
| mean pool over all vectors | 0.386 | 0.659 | 0 | 0 | 0.518 |
| max pool over all vectors | 0.659 | 0.977 | 0.1 | 1.0 | 0.791 |
| examples only (max) | 0.636 | 0.977 | 0.1 | 0 | 0.771 |
| blend 0.35 desc + 0.65 max example | 0.773 | 0.977 | 0 | 0 | 0.834 |
| discount max(desc, 0.9 x max example) | 0.659 | 0.977 | 0 | 1.0 | 0.754 |
| cap (example lift limited to desc + 0.1) | 0.682 | 0.932 | 0 | 1.0 | 0.750 |

The brief's warning about max pooling is empirically confirmed: pure max fires on a fresh negative control (and excluding inherited topics makes it worse, 0.2 FP, because the chaff was absorbing junk). The blend keeps the description as an anchor so an example query alone cannot carry an unrelated query over threshold. The 0.25 to 0.40 alpha range is a performance plateau (all at or above 0.75 accuracy, FP zero); 0.35 ships as the Setting-overridable default.

What shipped in production code: multi-vector index (INDEX_VERSION 2), blended scoring, alpha in Setting, margin rule in classify_query (default 0, Section 7), fingerprint now hashing description and example queries plus schema version, content pipeline splitting incoming blobs and finally using the package example_queries field that had been validated and ignored since the schema was written.

Results after the change, full production path:

| set | acc@1 | coverage | FP neg | FP unc | MRR |
|---|---|---|---|---|---|
| original labelled set | 1.0 | 1.0 | 0 | 0 | 1.0 |
| paraphrase probe | 0.795 | 0.977 | 0 | 0 | 0.823 |

The original-set 1.0 is the tautology of Section 3 and is reported because the acceptance criteria are defined there, not because it means generalisation. The honest improvement is 0.568 to 0.795 accuracy and 0.795 to 0.977 coverage on the probe.

Acceptance criteria, judged on the committed baseline and harness: false positives 0 before and after on the five negative controls (and 0 on all ten probe negatives). `are vaccines safe` still returns no card and is scored correct. `why does everyone use Google` resolves to Concentration and defaults. `how do I know if a website is reliable` resolves to Search literacy. Accuracy and coverage both improve on both instruments. Of the eight named recall gaps, all six that have approved prompts now serve the expected topic on the original phrasings; their paraphrases land correctly for four of six, with `why are product review sites all the same` and `why is reject all so hard to find` correct on original phrasing but adjacent-topic misses on paraphrase. Every open question is answered in Section 11.

Remaining probe failures are adjacent-topic judgement calls (Advertising and sponsored results catching a rank-higher query; Voice assistants catching a screen-reader phrasing), not absurdities. That texture matters: the pizza-to-politics era failures are gone; what is left is editorial boundary work.

## 6. The decision rule (Phase 4)

`docs/evaluations/decision-grid-2026-07-10.json`, threshold 0.30 to 0.55 by 0.025, margin 0 to 0.08, both instruments.

On the probe, false positives are zero everywhere at or above threshold 0.325 (with the Section 4 caveat that promptless topics do some of that work). Coverage at margin 0 is 0.98 at thresholds 0.325 to 0.35 and degrades slowly above. Every non-zero hard margin costs coverage with nothing to buy: margin 0.01 drops coverage to 0.86, margin 0.02 to 0.80.

So I recommend against the hard margin abstention the brief leaned toward, and I recognise this contradicts the drift of its own Section 4. The margin is real (Section 7 shows a fifth of decisions sit inside the noise band), but abstaining on them throws away correct answers wholesale: most sub-margin decisions are between semantic near-neighbours where either card would serve the reader. The honest use of the margin is as a display signal: the API now reports the top1-top2 gap and a margin_would_abstain flag on the debug endpoint, the Setting exists, serving honours it if ever set above zero, and the extension can render low-margin matches with softer language. The default stays 0.

The normalised signal (top1 minus corpus mean similarity) separates positives from negative controls slightly better than raw top1 (coverage 0.61 against 0.59 at FP-zero cutoffs, ignoring prompt availability), but both are far worse than the full pipeline, and per-topic thresholds would add 228 hand-tuned knobs of invisible state to a system whose failures this year have overwhelmingly been invisible state. Global threshold 0.35, margin 0, alpha 0.35 is the recommended operating point, and all three are now runtime-visible Settings with an audit trail.

## 7. Quantisation, or whether decisions are made of noise (Phase 5b)

`docs/evaluations/quantisation-2026-07-10.json`, 56 probe queries, 489-row corpus, blended scores.

| comparison | score delta sd | max | top-1 flips | top-3 set changes |
|---|---|---|---|---|
| int8 vnni vs fp32 | 0.0115 | 0.047 | 8 of 56 | 19 of 56 |
| int8 avx2 vs fp32 | 0.0115 | 0.052 | 3 of 56 | 24 of 56 |
| int8 arm64 vs fp32 | 0.0115 | 0.047 | 8 of 56 | 19 of 56 |
| O2 vs fp32 | 0.0000 | 0.000 | 0 | 0 |
| fp32 index + int8 queries, vs fp32 | 0.0084 | 0.036 | 2 of 56 | not measured |

Margins on the same queries: minimum 0.0007, median 0.057, 19.6 percent below 0.02, 44.6 percent below 0.05. So roughly one decision in five sits on a margin smaller than two standard deviations of quantisation noise, and the handoff's hypothesis is confirmed: a visible fraction of confident-looking rankings are numerical accidents. Add the cross-architecture result (sd 0.014 live-vs-replica) and the practical rules are:

- Rankings with margins under about 0.02 are runtime artifacts. Do not rewrite content to fix them; signal them (Section 6) or ignore them.
- Score time series are only comparable within one artifact on one architecture. MatchingEvaluation records artifact hash and runtime for exactly this reason.
- The brief's higher-precision-index idea works and is now measured: fp32 index with int8 queries cuts top-1 instability by three quarters because query-side noise is common-mode across topics and partially cancels. Its cost is loading the 90MB fp32 model during index rebuilds, which the 512MB free tier cannot absorb while serving (fp32 peak RSS 330 to 394MB before Django). Viable the day the service moves to a paid instance; not before.
- `model_O2.onnx` is bit-identical to `model.onnx` here, so the O2 entry in the candidate list buys ordering safety, nothing numerical.

Production should keep the int8 artifact on the free tier: 2.4 times faster per query (1.2ms against 3.0ms on this CPU), a quarter of the download, and 170MB peak RSS against 330 to 394. The noise it costs is real but bounded, characterised, and now visible in the API.

## 8. Honest confidence (Phase 5)

`docs/evaluations/calibration-2026-07-10.json`, fitted on the probe only (n=56; fitting on the contaminated set would teach the calibrator that high scores are always right).

Raw cosine displayed as a percentage has expected calibration error 0.17, and the direction is the interesting part: it understates. A card shown at "43 percent match" is right about 56 percent of the time; at "55 percent", about 71 percent. The current display simultaneously manufactures false precision (its third decimal is architecture noise, Section 7) and undersells the tool's actual reliability.

Platt scaling (a=11.53, b=−5.42) brings ECE to 0.067, and isotonic fits similarly, but a map fitted on 56 points from one runtime should not be shipped as a user-facing probability.

Recommendation, which is a design position as much as a statistics one: show no number. On a project whose central argument is that machine confidence is not correctness, a percentage on the card performs exactly the certainty the project critiques, and the data shows the number is neither precise (noise) nor honest (miscalibrated) nor even strategically self-serving (it understates). Show a qualitative connective phrase, at most a coarse band driven by the calibrated map (for example nothing below 0.45, "related" to 0.60, "closely related" above), and keep the Platt map internal for research use. If a number must appear for the evaluation period, it should be the calibrated one, labelled as an estimate, with at most two figures. This also resolves the third decimal problem by never showing one.

## 9. Encoders and the multilingual claim (Phase 6)

`docs/evaluations/encoders-2026-07-10.json`, probe, production blended scoring, each model with its own tokenizer and canonical pooling.

| encoder | acc@1 | coverage | FP neg | per-query | file | verdict |
|---|---|---|---|---|---|---|
| current multi-qa-MiniLM-L6 int8 | 0.795 | 0.977 | 0 | 1.2ms | 23MB | keep |
| all-MiniLM-L12-v2 fp32 | 0.818 | 1.0 | 0 | 5.7ms | 133MB | +2 points, 2 to 4x cost; revisit on paid tier |
| bge-small-en-v1.5 fp32 | 0.75 at t=0.59 | 1.0 | 0 only at t≥0.59 | 6.3ms | 133MB | score range incompatible, no win |
| gte-small fp32 | n/a | n/a | never 0 up to t=0.75 | 6.3ms | 133MB | disqualified |
| paraphrase-multilingual-MiniLM-L12 int8 | 0.773 | 0.977 | 0.1 | 2.6ms | 118MB | fails free tier (565MB peak RSS) |

The asymmetric-retrieval worry in the brief (multi-qa is question-to-passage, our use is increasingly question-to-question) does not show up as a deficit worth paying for: the question-tuned L6 beats two general-purpose small encoders and sits 2 points behind a model twice its depth.

Open question 7, answered with a number rather than a hope: the current encoder scores 0 of 8 on French, Spanish and Portuguese paraphrases of well-covered topics. The multilingual claim in the funding case is not supportable today and should not survive review as written. The honest version is supportable: a quantised multilingual encoder reaches 5 of 8 zero-shot against the unchanged English corpus, which is genuinely impressive cross-lingual behaviour, but it costs 2 points of English accuracy, needs threshold recalibration, and peaks at 565MB RSS during index build, above the whole free instance. Suggested funding-case language: "the architecture supports multilingual matching by swapping one encoder, demonstrated at small scale; resourcing this properly is part of what the grant funds", with the 5-of-8 table as evidence. That converts an unsupportable claim into a fundable work package.

## 10. Serving fixes that rode along (Phase 7)

Priority now orders displayed prompts again (similarity still selects which appear); the 26 June editorial ladder was inert on the classifier path from the day semantic ranking shipped, and Emilia's ordering findings are now implementable as five-minute editorial decisions. The pre-apply guard scores incoming package topics against the existing index at preview time and names, quantifies and lists every collision above threshold, advisory rather than blocking. `scripts/calibrate_threshold.py` is retired; the harness and the decision grid are the two remaining ways to answer its question, which is one way per question.

## 11. The nine open questions, answered

1. Which pooling? Blend alpha times description plus (1 minus alpha) times best example query, alpha 0.35, plateau 0.25 to 0.40. Max pooling is measurably dangerous (fires on negative controls); mean pooling dilutes (0.39 accuracy); the blend wins on both instruments. Section 5 table.
2. How much do embedded contrasts hurt? Modestly and asymmetrically. Stripping them from the single blob: accuracy 0.568 to 0.591. Within the blend they barely matter (0.773 against 0.750 at matched alpha) because the example anchor dominates. The editorial argument for splitting them out stands regardless: they now cannot hurt, and they never help.
3. Inherited topics: index, exclude, or synthesise? Index them, and the reason is not what anyone expected: they are load-bearing chaff. Excluding them raises accuracy slightly (0.659 to 0.682 baseline) but doubles max-pool false positives (0.2 to 0.4), because junk queries that used to be absorbed by promptless inherited topics land on described ones instead. They also carry real matches (`are vaccines safe` reaches Vaccination on a name embedding alone). The 193-descriptions project should proceed, but as a curated priority list (which inherited topics earn descriptions and prompts) rather than a bulk-writing exercise, and every batch must pass the pre-apply guard, because describing a topic promotes it from chaff to competitor. The unapplied 23-topic batch from 21 June should be guard-probed and consciously decided on, after 24 July.
4. Global threshold, per-topic, or normalised? Global, 0.35, unchanged. The normalised signal helps marginally at the extremes and is worth revisiting when Emilia's set arrives; per-topic thresholds are 228 knobs of invisible state and the evidence cannot justify them. Section 6.
5. What should the card display? Nothing numeric. Qualitative connective phrasing, optionally banded by the internal calibrated map. Raw cosine is miscalibrated (ECE 0.17, understating), carries architecture noise in its low digits, and performs the exact certainty the project critiques. Section 8.
5b. Is quantisation noise bigger than decision margins? For one decision in five, yes (margins under 0.02 against noise sd 0.0115, cross-runtime sd 0.014). Confirmed, quantified, and answered structurally: margin display signal, artifact-stamped evaluations, hybrid index option costed for a paid tier. Section 7.
6. Different encoder? No for the free tier. all-MiniLM-L12-v2 is the only candidate that wins anything (+2 points) and costs 2 to 4x latency and 6x size. Revisit alongside any Render upgrade decision. Section 9.
7. Multilingual? Not supportable today (0 of 8). Supportable as a costed work package with a multilingual encoder on a bigger instance (5 of 8 zero-shot, measured). Rewrite the funding-case sentence. Section 9.
8. What should a good labelled set contain, and how big? Rough power arithmetic, stated so it can be argued with. To bound the false-positive rate below 2 percent with 95 percent confidence given zero observed false positives needs about 150 negative controls (rule of three); the current five bound it only below about 45 percent, so today's FP-zero claims are weak evidence. To detect a 10-point accuracy difference between two configurations with 80 percent power at alpha 0.05 in a paired design needs on the order of 150 to 200 informative (discordant-capable) positives, so 300 to 500 labelled positives at realistic discordance rates. Emilia's set should therefore target roughly 500 rows: 300 to 350 positives spread over the ten query shapes (ordinary, messy, navigational, transactional, question-form, keyword-form, sensitive, ambiguous, multilingual tokens, misspelled), at least 150 negative controls weighted toward navigational and transactional junk, a deliberate uncovered-topic band, and no query copied from any example list, checked programmatically the way the probe was. Label served-or-not and correct-topic separately, and record the live top-3 with scores at labelling time so future runs can measure drift against the same rows.
9. The section I was not asked for: the load-bearing accident. The system's zero false positives depend on junk queries landing on topics that happen to have no approved prompts. That is not a safety property; it is an inventory coincidence. Two consequences. First, every future prompt-writing batch changes the false-positive surface, so the FP metric must run in the pre-apply guard, not just in evaluations after the fact (it now does). Second, the honest metric for the paper is false positives among queries whose nearest topic HAS approved prompts, which today is 0 on both instruments but measured on tiny negative sets; the 150-negative-control requirement in answer 8 exists to make that number publishable.

## 12. Recommendations, ordered by expected gain per unit of risk

1. Deploy the branch (harness, split representation, blended scoring, guard, priority fix). Everything is behind tests (63 passing), the public API shape is unchanged, and the acceptance criteria pass. Risk: low. Gain: the probe accuracy and coverage numbers in Section 5, plus every future content decision getting a guard.
2. Adopt the labelled-set specification (answer 8) as Emilia's brief for her remaining two weeks. Her set is the durable deliverable; its design decides whether every number after 24 July means anything. Risk: none. Gain: statistical validity.
3. Change the card's confidence display to non-numeric phrasing (answer 5). One extension string change plus one API field already present. Risk: trivial. Gain: interface honesty aligned with the project's argument, and a defensible answer to the reviewer who asks what the percentage means.
4. Decide the 23-topic unapplied batch and the 193-descriptions project through the guard, post-24-July, as curated batches (answer 3). Risk: managed by the guard. Gain: the largest content backlog gets a data-driven triage instead of a bulk apply.
5. Funding-case language fix for multilingual (answer 7). Risk: none. Gain: removes a claim a reviewer can falsify with one query, replaces it with a measured work package.
6. On any move to a paid Render instance: pin the artifact choice deliberately (int8 for speed or O2-fp32 for numerical stability), consider the fp32-index hybrid (75 percent less rank instability, measured), and re-run the encoder comparison including L12. Risk: low. Gain: kills the score-noise class of confusion for the research record.

## 13. What I could not verify, and what it would take

Which ONNX artifact Render downloaded at its last build: needs one line from the Render deploy log (dashboard, last build, look for the "Trying onnx/..." lines). Real Render memory headroom during an index rebuild, real Neon latency, and real free-tier wall times: not observable from outside; the numbers here are from this sandbox's CPU and are labelled as such. Whether production's onnxruntime version matches 1.23.2: requirements allow any 1.2x; the deploy log or a pip freeze on the instance would say. The live service's exact behaviour under the new code: verified only through the test suite and the offline harness; the four gate queries and the probe should be spot-checked against the live debug endpoint after deploy, using decision-level comparison, not score-exact. And the deepest one: whether 56 paraphrases written by one author generalise the way 500 real searches will; that is Emilia's fortnight, and recommendation 2 is designed to answer it.
