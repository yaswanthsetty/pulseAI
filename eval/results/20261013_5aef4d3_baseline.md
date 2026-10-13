# Phase 2 evaluation baseline — snapshot `20261011_18b35cf`

Assembled 2026-10-13T16:31:04.004424+00:00 at `5aef4d3` — queries `eval/queries.jsonl` sha256 `393f6e562490…`.

No quality target is set in Phase 2: these are the numbers later phases are graded against. Unjudged chunks count as 0 everywhere; every metric with an undefined denominator is excluded from its macro-average instead of being coerced to 0.

Suites present: `retrieval`, `rag`, `clustering`, `quality`

_(suite git sha: `5090a0e`, mock=False)_

## Retrieval ablation (macro-averaged, snapshot pool grades)

Queries: 60 · pool depth 10 · mean pool size 48.870 (median 49.500) · unjudged chunks count as 0.

| config | recall@5 | recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| hybrid-norank-notemporal | 0.735 | 0.750 | 0.950 | 0.598 |
| hybrid-norank-temporal | 0.710 | 0.725 | 0.933 | 0.587 |
| hybrid-rerank-notemporal | 0.703 | 0.720 | 0.906 | 0.576 |
| hybrid-rerank-temporal | 0.709 | 0.725 | 0.914 | 0.576 |
| keyword-norank-notemporal | 0.733 | 0.737 | 0.925 | 0.586 |
| keyword-norank-temporal | 0.733 | 0.737 | 0.912 | 0.580 |
| keyword-rerank-notemporal | 0.714 | 0.725 | 0.900 | 0.576 |
| keyword-rerank-temporal | 0.709 | 0.728 | 0.894 | 0.575 |
| semantic-norank-notemporal | 0.699 | 0.708 | 0.897 | 0.571 |
| semantic-norank-temporal | 0.699 | 0.708 | 0.889 | 0.568 |
| semantic-rerank-notemporal | 0.700 | 0.709 | 0.889 | 0.568 |
| semantic-rerank-temporal | 0.698 | 0.706 | 0.881 | 0.565 |

Article-level projection of the same rankings (any grade-2 chunk in the article counts as a hit; the unit the RAG path actually cites):

| config | recall@5 | recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|
| hybrid-norank-notemporal | 0.961 | 0.983 | 1.000 | 0.974 |
| hybrid-norank-temporal | 0.961 | 0.983 | 1.000 | 0.973 |
| hybrid-rerank-notemporal | 0.947 | 0.964 | 0.983 | 0.952 |
| hybrid-rerank-temporal | 0.944 | 0.961 | 0.975 | 0.948 |
| keyword-norank-notemporal | 0.972 | 0.978 | 0.992 | 0.967 |
| keyword-norank-temporal | 0.972 | 0.978 | 0.979 | 0.957 |
| keyword-rerank-notemporal | 0.952 | 0.964 | 0.992 | 0.954 |
| keyword-rerank-temporal | 0.944 | 0.969 | 0.985 | 0.953 |
| semantic-norank-notemporal | 0.961 | 0.978 | 0.992 | 0.965 |
| semantic-norank-temporal | 0.961 | 0.972 | 0.983 | 0.956 |
| semantic-rerank-notemporal | 0.964 | 0.972 | 0.983 | 0.957 |
| semantic-rerank-temporal | 0.961 | 0.969 | 0.975 | 0.954 |

_(suite git sha: `f698fae`, mock=False)_

## RAG fast vs deep

Judge: `qwen2.5:7b@rubric:ed2749998613` (requested `qwen2.5:14b`; fell back; errors: 0; cached judgments 260) · refusal detection is deterministic regex first, judge only for borderline cases.

| metric | fast | deep |
|---|---|---|
| citation_precision | 1.000 | 1.000 |
| citation_coverage | 0.273 | 0.030 |
| required_fact_recall | 0.514 | 0.447 |
| invalid_citation_rate | 0.000 | 0.000 |
| refusal_rate | 0.117 | 0.500 |
| refusal_when_evidence_exists_rate | 0.091 | 0.303 |
| refusal_without_evidence_rate | 0.400 | 0.741 |
| judge_routed_count | 16 | 6 |
| sub_question_relevance | — | 0.765 (n=238, err=0) |

| path | cold (ms) | warm p50 (ms) | warm p95 (ms) |
|---|---|---|---|
| fast | 104668 | 10991 | 15099 |
| deep | 34006 | 43854 | 50344 |

_(suite git sha: `f88023a`, mock=False)_

## Clustering vs the labelled event set

Label set: eval/event_labels.json (agent_drafted_pending_20pct_review) — 29 articles in 18 true events (5 cross-source).

| metric | value |
|---|---|
| slow-path input (unmatched articles) | 4 |
| slow-path ARI / NMI / purity (labelled members) | — / — / — |
| combined ARI / NMI / purity | 0.107 / 0.628 / 0.379 |
| fast-path centroid precision (threshold 0.720) | 0.357 (10/28) |
| note | fewer than 2 labelled articles were in the slow-path input; ARI/NMI/purity are unscoreable (not 1.0) |

_(suite git sha: `5aef4d3`, mock=False)_

## Content quality (held-out, freshly ingested chunks)

Held-out set: 50 chunks from a new ingest (TechCrunch 21, BBC 15); labels: agent_drafted_two_passes_pending_review (self-consistency disagreements 0).

| level | precision (95% CI) | recall (95% CI) | TP/FP/FN |
|---|---|---|---|
| chunk | undefined (0.000-1.000) | 0.000 (0.000-0.390) | 0/0/6 |
| article | 1.000 (0.207-1.000) | 0.250 (0.046-0.699) | 1/0/3 (32 articles) |

Threshold trigger (precision < 0.95): **FIRED** — precision is undefined (the classifier predicted no boilerplate in this set: 0 true positives, 6 missed) — the threshold trigger fires on recall, not on a fabricated precision.
