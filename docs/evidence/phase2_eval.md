# Phase 2 Evidence — Evaluation Harness (the measured baseline)

Date: 2026-10-13 — Baseline: `eval/results/20261013_5aef4d3_baseline.{json,md}` at `5aef4d3`
(snapshot `20261011_18b35cf`, 80 articles / 397 chunks; queries `eval/queries.jsonl`
sha256 `393f6e562490…`).

Scope: turn every claim about retrieval and answer quality into a number we can
regress on (plan `docs/plans/phase2_eval.md`). No pipeline, model or threshold
was changed: Phase 2 **measures** the two known deep-path defects and the
classifier's real accuracy, and leaves the fixing to Phases 3–5.

## 1. What exists now

| Piece | Where | What it does |
|---|---|---|
| Retrieval suite | `eval/retrieval_suite.py` | 12 ablation configs (dense/sparse/hybrid × rerank × temporal) over the blinded pool grades, chunk- and article-level |
| RAG suite | `eval/rag_suite.py` | fast + deep chat paths, citation/refusal/fact/latency metrics, judge cache |
| Clustering suite | `eval/clustering_suite.py` | slow path (UMAP+HDBSCAN) and fast path (centroid matching) against a labelled event set |
| Content-quality suite | `eval/quality_suite.py` | held-out classifier precision/recall with 95% Wilson intervals |
| Runner | `eval/run.py` | `--suite retrieval\|rag\|clustering\|quality\|all\|ci`, `--baseline` comparison |
| Baseline assembler | `eval/report.py` | one JSON + markdown baseline, `eval/results/latest.json` pointer |
| CI gate | `.github/workflows/ci.yml` | mocked 12-query subset, **exact equality** against `eval/results/baseline_ci.json` |
| Judge | `eval/judge.py`, `eval/prompts/judge.md` | local Ollama only, frozen rubric, version recorded in every results file |
| Judge agreement | `eval/judge_agreement.py` | blinded 20% sample export + κ report (human gate, §6) |

## 2. The baseline in one page

**Retrieval** (60 queries, pool depth 10, mean pool 48.9, unjudged chunks count as 0):

| config | recall@5 | recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| hybrid-norank-notemporal | 0.735 | 0.750 | 0.950 | **0.598** |
| keyword-norank-notemporal | 0.733 | 0.737 | 0.925 | 0.586 |
| hybrid-rerank-notemporal | 0.703 | 0.720 | 0.906 | 0.576 |
| semantic-rerank-temporal (production default) | 0.698 | 0.706 | 0.881 | 0.565 |

Article-level projection of the same rankings: nDCG@10 **0.948–0.974**,
recall@10 0.961–0.983, MRR@10 0.975–1.000.

**RAG** (60 queries × 2 paths):

| metric | fast | deep |
|---|---|---|
| citation_precision | 1.000 | 1.000 |
| citation_coverage | 0.273 (n=55) | 0.030 (n=33) |
| required_fact_recall | 0.514 | 0.447 |
| invalid_citation_rate | 0.000 | 0.000 |
| refusal_rate (blended) | 0.117 | 0.500 |
| **refusal_when_evidence_exists_rate** | **0.091** (5/55) | **0.303** (10/33) |
| refusal_without_evidence_rate | 0.400 (2/5) | 0.741 (20/27) |
| sub_question_relevance | — | 0.765 (n=238, 0 judge errors) |
| cold / warm p50 / warm p95 (ms) | 104,668 / 10,991 / 15,099 | 34,006 / 43,854 / 50,344 |

**Clustering** (29 labelled articles in 18 true events, 5 cross-source):
slow-path input only 4 unmatched articles → 0 clusters, 4 noise, ARI/NMI/purity
**unscoreable** (1 of 29 labelled articles inside the input); combined partition
ARI 0.107 / NMI 0.628 / purity 0.379; fast-path centroid precision **0.357**
(10/28 judgeable matches).

**Content quality** (held-out): 50 pre-filter chunks (TechCrunch 21, BBC 15)
from 79 freshly ingested articles (357 candidate pieces), two-pass labels:

| level | precision (95% CI) | recall (95% CI) | TP/FP/FN |
|---|---|---|---|
| chunk | **undefined** (0.000–1.000) | **0.000** (0.000–0.390) | 0/0/6 |
| article | 1.000 (0.207–1.000) | 0.250 (0.046–0.699) | 1/0/3 (32 articles) |

Threshold trigger (precision < 0.95): **FIRED**.

## 3. The two known deep-path defects, as numbers (exit criterion 3)

- **T1-deep — refusing with evidence in hand.** Deep refuses on **0.303** of the
  queries that retrieved at least one grade-2 chunk (10/33) against **0.091**
  for the fast path (5/55). The blended 0.500 deep figure is *mostly* the
  healthy half: 20 of the 30 deep refusals happen on queries with no grade-2
  chunk retrieved, where refusing is correct. Splitting the metric was required
  to make the defect visible at all — the first version of this table reported
  the blended rate and hid it.
- **T3-deep — citation coverage and sub-question relevance.** Deep cites
  **3.0%** of the grade-2 chunks it retrieved (fast: 27.3%), and its planner's
  238 sub-questions score **0.765** mean relevance (fast path has no planner;
  no judge errors). Per type, the worst sub-question relevance is on
  multi-hop/compare queries — the same types with the worst required-fact
  recall (0.256 / 0.267 deep) and the highest refusal-when-evidence rates.

Both defects are now regressable: any Phase 4/5 change shows up as a delta in
these rows, not as an impression.

## 4. Judge provenance and cost

- **Effective judge: `qwen2.5:7b@rubric:ed2749998613`** — the plan's preferred
  `qwen2.5:14b` exceeded the 20 s budget on this machine (50.1 s on its first
  judgment, per the judging log), so the documented fallback fired. The results
  file records the requested model, the effective model and
  `fallback_used: true`, and the *version* string names the model that actually
  answered. The fallback *reason* is a runtime artifact a cache replay cannot
  reproduce, so `fallback_reason` is null in the committed file — the reason
  lives in the judging log, not in the number.
- **260 judgments, 0 errors**, covering 183 distinct (task, item text) pairs —
  repeated sub-questions are judged once.
- **The judgments are cached** (`eval/work/<snapshot>_rag_judge.json`, untracked)
  and the cache is written incrementally. The judging pass cost hours of wall
  clock; re-scoring the whole suite from a complete cache takes **0.6 s and
  makes zero model calls**, which is how the final results file was produced.
- **κ is not yet known.** The blinded 20% sample (52 items,
  `eval/judge_agreement.jsonl`) is exported and committed with
  `human_score: null`; the κ ≥ 0.70 gate is a **human** step. Until it passes,
  every judge-derived number above (sub-question relevance, judge-routed
  refusals) is unverified — reported, not trusted.

## 5. Held-out content quality vs the in-sample fixture

The Phase 1.2 fixture (`tests/fixtures/boilerplate_labels.jsonl`, P=0.984 /
R=0.969) is in-sample: the weights and `STRONG_MARKERS` were fit on those same
102 labels. On the held-out set the same classifier predicted **zero**
boilerplate pieces out of 50 and missed 6 — chunk-level precision is undefined
(no positive predictions, so 0.000 would have been a fabrication) and recall is
0.000. The ingest that produced the set confirms it independently: 79 new
articles yielded **357 persisted chunks from 357 candidate pieces**, i.e. the
filter rejected nothing.

This is the trigger's intended outcome, not a regression from Phase 1.2: the
honest number simply was never measured before. The threshold is *not* retuned
here (Plan §6: no pipeline changes in Phase 2); the retune is Phase 5 work
against this baseline. The in-sample fixture stays as a regression guard.

## 6. Exit criteria (plan §6) and the open human gates

1. **Full suite end to end on snapshot #1** — yes: retrieval, rag, clustering and
   quality each run from one command, and `--suite all` chains them.
2. **Baseline JSON + markdown committed** — yes, at
   `eval/results/20261013_5aef4d3_baseline.{json,md}` (+ `latest.json`).
3. **T1-deep and T3-deep appear as numbers** — yes, §3.
4. **Coverage ≥ 80%, CI green (exact-equality harness gate)** — `uv run pytest -q
   --cov=backend --cov-fail-under=80`: **469 passed, coverage 80.87%**, ruff and
   `lint-imports` (4/4 contracts) clean, and the mocked CI subset matches
   `eval/results/baseline_ci.json` exactly (`baseline check: EXACT match`) — all
   verified locally on 2026-10-13 at `5aef4d3`.

Open human gates, deliberately **not** closed by this work:

- `eval/queries.jsonl` is still **`draft_unreviewed`**: 12 of the 60 queries
  (`eval/review_sample.txt`) await Yaswanth's approval, so the query set is not
  frozen and every number above is provisional. The retrieval and RAG results
  files record `status: draft_unreviewed` so nobody can mistake them for frozen
  numbers.
- `eval/heldout_labels.json` and `eval/event_labels.json` are agent-drafted and
  pending the 20% review; the quality labels are a *self-consistency* check
  (same labeller, two passes, 0 disagreements), never inter-annotator agreement.
- Judge κ ≥ 0.70 (§4).

## 7. Honest caveats

- **The clustering slow path is effectively unmeasured.** The fast path absorbs
  almost every article (28 of 29 labelled articles already sat in an event), so
  only 4 unmatched articles reach UMAP+HDBSCAN; ARI/NMI/purity are unscoreable
  rather than 1.0. The combined partition (ARI 0.107 / purity 0.379) is the
  number that exposes the real problem: the fast path matches a lot and is right
  only 36% of the time.
- **Chunk-level retrieval is far worse than article-level retrieval**
  (nDCG@10 0.565 vs 0.974 on the same rankings). The evidence the deep path
  cites is the chunk list, which is exactly why deep citation coverage is 3%.
- **Cold latency is one sample per path** (first query in a fresh process), not
  a distribution; warm p50/p95 are over 59 runs. Deep's warm p50 of 43.9 s is
  the fixed planner→retriever→reasoner→synthesizer sequence running on
  `qwen2.5:3b` on this machine, not a harness artifact.
- **Judge metrics are single-judge, single-run**, with the fallback model
  (7b) rather than the preferred one, and no κ yet.
- **`required_fact_recall` is a substring match** over 1–3 literal facts per
  query; it cannot see a correct answer phrased around the literal string.

## 8. Reproduce

```bash
# full suite (needs Docker + Ollama + the pinned snapshot)
uv run python -m eval.run --suite all

# re-score the RAG suite from the judge cache (no model calls)
uv run python -m eval.rag_suite score

# assemble the committed baseline from the newest per-suite files
uv run python -m eval.report --print

# the CI harness gate (mocked, deterministic, exact equality)
uv run python -m eval.run --suite ci --baseline eval/results/baseline_ci.json
```
