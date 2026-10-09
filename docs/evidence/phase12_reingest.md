# Phase 1.2 Evidence — Chunk-Filter Refit, Safe Re-ingest, Corpus Cleanup

Date: 2026-10-09 — Commit: see git history for the Phase 1.2 series.

Scope: validate the boilerplate chunk classifier against hand labels (precision-first),
re-extract the corpus through trafilatura with a hardened fallback, re-ingest safely
with `pulseai-reingest`, and re-measure retrieval smoke quality.

## 1. Classifier validation (precision-first)

**Fixture:** `tests/fixtures/boilerplate_labels.jsonl` — 102 hand-labelled rows (65 junk / 37 clean),
stratified across all four sources and the 0.40–0.60 score band. A unit test asserts the row and
class counts and the exact confusion matrix, so any threshold/weight change is measured, not guessed.

**Confusion matrix (final classifier, `is_boilerplate`):**

| | predicted junk | predicted clean |
|---|---|---|
| **labelled junk** | TP = 63 | FN = 2 |
| **labelled clean** | FP = 1 | TN = 36 |

- **Precision = 0.984** (≥ 0.95 constraint met) — losing real body text is bounded to 1 in 64 deletions.
- **Recall = 0.969**.
- The single FP is a Samsung Galaxy Z Fold review specs chunk that repeats a 5-gram ≥3×; it sits on the
  deliberate span-repetition boundary (raising the bar to ≥4× would let 12 markerless TechCrunch card
  rails through, which is worse).
- Both FNs are TechCrunch card-list chunks (junk kept — the acceptable failure direction).

## 2. Production bugs found and fixed during the work

1. **Trafilatura 2.3.1 process-global dedup cache** (`parser.py`): `deduplicate=True` uses a global
   document-hash LRU, so the *n*th identical HTML document silently returned `None` and the parser
   silently downgraded to the BS fallback — extraction became nondeterministic across requests.
   Fixed with a per-call scoped `LRUCache` (segment dedup preserved) + regression test
   (`tests/unit/test_parser.py::test_repeated_extraction_is_deterministic`).
2. **FakeQdrant masked a real API mismatch**: the fake's `delete` signature differed from the real
   client (`points_selector=`), hiding a keyword typo that crashed the first real re-ingest run.
   Fixed the typo and tightened the fake to the real signature.
3. **Per-article crash isolation**: one article's failure aborted the whole re-ingest run; the CLI now
   rolls back, reports `failed`, and continues (embed-then-swap already kept the DB consistent).
4. **Float knife-edge in the classifier**: `0.35 + 0.10` landed at 0.44999999999999996 < threshold.
   Scores are now rounded before comparison.
5. **`body_is_usable` over-triggered on procedural prose** (how-to articles repeating "click Settings"
   tripped the whole-body repetition clause) — replaced with a window-based majority-junk gate.
6. **Windows Hyper-V excluded port ranges** shifted after a host reboot (5433–5532 and 8069–8268),
   breaking Postgres 5434 and the smoke backend 8090. Compose now parameterizes the host port
   (`POSTGRES_HOST_PORT`, set to 5540) and `tests/conftest.py` honors `.env`'s `POSTGRES_PORT`
   instead of hard-forcing 5434.

## 3. Re-ingest run (80 articles)

CLI: `uv run pulseai-reingest` — dry-run first, then real. Safety contract held on every path:
embed-then-swap (new vectors upserted before any delete), transaction rollback on failure,
fetch-failure keeps stored text with only the chunk filter applied, `--dry-run` writes nothing.

| Pass | Result |
|---|---|
| Dry-run | per-article diffs, 0 writes |
| Full run | **28 updated, 47 unchanged, 5 failed** (body-less BBC iPlayer pages: re-fetch yielded no prose, old vectors kept), 4 throttled fetches safeguarded by the keep-stored guard |
| Targeted prune (dry-run → real) | **5 pruned** — every old chunk classifier-confirmed junk, so the 17 hand-verified junk vectors were deleted and the articles converged to `summary`/`low` (the state a fresh ingest of those URLs produces) |

## 4. Before / after (same final classifier on both sides)

| Metric | Before | After |
|---|---|---|
| Articles | 80 | 80 |
| Chunks | 519 | 397 |
| Qdrant vectors | 519 | **397 = DB count** (no orphans) |
| Boilerplate chunks (final classifier) | 78 (**15.0%**) | **0 (0.0%)** |
| — BBC Science & Tech | 84 chunks, 21.4% junk | 64 chunks, 0% |
| — TechCrunch AI | 128 chunks, 46.1% junk | 58 chunks, 0% |
| — The Guardian Technology | 198 chunks, 0.5% junk | 176 chunks, 0% |
| — The Verge | 109 chunks, 0% junk | 99 chunks, 0% |
| Extractor mix | (pre-refactor ingest) | **75 trafilatura / ok + 5 summary / low** |
| `extraction_quality='low'` | — | 5 (the pruned iPlayer pages, honestly labelled) |

_Note: the first v1-classifier snapshot measured 105/519 = 20.2% (TechCrunch 53.9%); the table above
re-scores the pre-reingest dump with the final refit classifier so both sides use identical
measurement._

**Hand-check:** 30 chunks sampled stratified (seed 7; 6 BBC / 6 TC / 10 Guardian / 8 Verge, incl. the
former `008dc616` junk chunk's article): **30/30 genuine article prose, 0 junk**. The only borderline
item is a TechCrunch episode show-notes list ("Listen to the full episode to hear more about: …"),
which is legitimate episode content, not site chrome.

**The BBC `008dc616` regression chunk** (the Guardian-style "Follow / close panel" junk that T3-deep
once cited for the fact "Microsoft") belonged to article `a70516a1` ("Amazon is using Twitch to train
generative AI") whose entire stored body was that junk — pruned: 0 chunks, `summary`/`low`.

## 5. Smoke reruns (9 runs, cleaned corpus)

Same 9 questions as `phase1_smoke.md`, backend on `127.0.0.1:8300` (8090 now inside a Hyper-V
excluded range), fresh analyst user, identical build/config (`OLLAMA_URL=:16000`, `CHAT_NUM_CTX=8192`).

| Topic | Path | Latency | (a) body-grounded | (b) [#n] maps | (c) no error/500 |
|---|---|---|---|---|---|
| Claude watermarks | chat | **45.6s (cold)** | PASS | PASS | PASS |
| Claude watermarks | chat (deep) | 34.2s | **FAIL** (synthesizer refusal — see below) | PASS | PASS |
| Claude watermarks | report | 18.6s | PASS | PASS | PASS |
| Amazon rare texts | chat | 11.1s | PASS | PASS | PASS |
| Amazon rare texts | chat (deep) | 41.4s | PASS | PASS | PASS |
| Amazon rare texts | report | 25.1s | PASS | PASS | PASS |
| Meta open AI deal | chat | 11.1s | PASS | PASS | PASS |
| Meta open AI deal | chat (deep) | 37.0s | PASS* (see T3-deep note) | PASS | PASS |
| Meta open AI deal | report | 32.4s | PASS | PASS | PASS |

**Summary: (a) 8/9 (T3-deep literal-pass with caveat), (b) 9/9, (c) 9/9.** Metrics after the 9 runs:
`chat_fast=3, chat_deep=3, report=3`, `pulseai_invalid_citations` = 0, `pulseai_context_chunks_dropped` = 0.

Latency: first run 45.6s cold (model load); warm runs 11.1–41.4s chat / 18.6–32.4s report —
in line with the pre-fix run (11.7–60.1s chat; deep runs are now *faster*: 34.2/37.0/41.4s vs 39.2/44.7/60.1s).

Check (a) quotes (body-only facts, absent from titles):

- **T1-fast**: "applied at a granular, random level … undetectable by average readers" and
  "EU AI Act's Transparency Code" — Guardian/TC article bodies (top evidence scores 0.93/0.96;
  previously the same question's evidence led with pure site navigation).
- **T2-fast**: "purchases books through commercial channels"; the Anthropic authors' lawsuit ruling —
  TC body chunk `77abccb7`.
- **T2-deep**: "model collapse"; "cut off from their spines and scanned" — TC body `77abccb7` @0.999.
- **T3-fast**: "Glimmer, an open-weight AI model … contrast with Muse Spark … behind closed APIs" —
  TC body `1065415d` @1.0 (was previously starved by boilerplate-first chunks).
- **T1/T2/T3-report**: top-10 sources are article-body chunks (scores 0.96–0.99 top-1); summaries
  quote body-only facts ("Transparency Code", VGT3 tracking device, 6,500-word manifesto).

### T3-deep re-score (condition from the plan)

Verdict: **the answer now quotes article prose, not a related link.** The old PASS* cited "Microsoft"
from a related-links boilerplate line; the rerun answers from the body of a real article (chunk
`2459b814`, Guardian "Our children are less cognitively capable…": the IQ-decline-since-2000 detail is
body-only). Caveat: the run is still a poor answer to the question, for a *new* reason recorded below.

### Findings for Phase 2 (deep path — not caused by the re-ingest)

1. **T1-deep still refuses**, but the cause moved: its top evidence is now a *clean* Guardian article
   body at score 0.97 — retrieval is no longer starved by boilerplate. The planner→reasoner→synthesizer
   hand-off drops the evidence (planner ran 2×, reasoner 4×, and the synthesizer reports the partial
   answers don't cover the question). This is orchestration/prompting quality, not corpus quality.
2. **Planner sub-question hallucination (T3-deep)**: the planner invented a sub-question about
   "Jared Horvath's book *The Digital Delusion*" — unrelated to the Meta $250M question — retrieval
   faithfully matched it to the Guardian screen-time article at 0.97, and the synthesizer honestly
   said the sub-question was off-topic. Both deep-refusal symptoms now sit in the deep-path planner,
   the single highest-leverage Phase 2 target.

## 6. Gates

- `ruff check .` — clean.
- Full suite: see the commit's CI run; locally all unit + integration tests pass with
  coverage ≥ 80% (`--cov-fail-under=80`).
