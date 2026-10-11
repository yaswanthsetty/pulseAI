# Phase 2 — Evaluation Harness (Plan)

Status: **APPROVED 2026-10-11** with the amendments recorded in §8 (pooling,
labelling protocol, refusal detection, exact-equality CI gate, exit criteria).
Date: 2026-10-11 — Baseline commit: `c3fc039` (plan amended on `5f6e91d`+).

Goal: turn every claim about retrieval and answer quality into a number we can
regress on. Before Phase 2 ships, "the answers are grounded" must be a metric,
not an impression.

Non-goals for Phase 2: no pipeline changes, no new models, no deployment work
(that is Phase 8). Fixes for the deep-path defects found in Phase 1.2 are
*measured* here and *fixed* only after the harness can prove them.

---

## 1. Dataset

### 1.1 Corpus snapshot

Everything is frozen against a snapshot so results stay comparable as the
corpus grows:

- `eval/snapshots/<snapshot_id>.json` where `snapshot_id = <yyyymmdd>_<git sha7>`.
- The snapshot records, for every article: `article_id`, `source_id`,
  `published_at`, `extractor`, `extractor_version`, `extraction_quality`, and
  the ordered list of its `chunk_id`s (with `chunk_number` and token counts).
- The current clean corpus (80 articles / 397 chunks at `c3fc039`, boilerplate
  0.0%) is snapshot **#1**. New ingests do not silently extend the snapshot;
  adding corpus means minting a new snapshot id and reporting both.

### 1.2 `eval/queries.jsonl` — 60–100 queries

One JSON object per line:

```json
{
  "id": "q017",
  "snapshot": "20261011_c3fc039",
  "query": "What throughput did AWS report for the ... migration?",
  "type": "fact | compare | multi-hop | time-bound",
  "language": "en",
  "path_hint": "fast | deep",
  "relevant_article_ids": ["..."],
  "relevant_chunk_ids": ["..."],
  "required_facts": ["substring or regex the answer must contain"],
  "relevance_labels": {"<chunk_id>": 2, "<other_chunk_id>": 0},
  "notes": "human labeler, date"
}
```

- **Coverage:** ~25 `fact`, ~20 `compare`, ~20 `multi-hop`, ~15 `time-bound`;
  every query's answer must live in the frozen snapshot (no answer that the
  current corpus cannot support).
- `required_facts` are 1–3 short literal strings (a number, a named entity, a
  date) sufficient to decide "the answer contains the fact".
- `relevance_labels` use a 3-level grade (2 = directly answers, 1 = topically
  related, 0 = irrelevant) so nDCG@10 has real work to do.

### 1.3 Labelling with pooling — and who labels

The system under test must never help author its own *queries*, but relevance
grades are built with a standard **pool** so that relevant chunks the labeler
never read are not silently missing (which would corrupt Recall and nDCG):

1. **Query authoring.** Queries and their `required_facts` are written from
   the *source article text* (and the original feed entries), never from
   retrieval output. Concretely: **the coding agent drafts the queries and
   `required_facts` from article text; Yaswanth reviews a random 20% of the
   draft set and must approve it before the set is frozen.** Nothing is
   frozen without that approval.
2. **Pooling for relevance labels.** Hand-grading all 397 chunks per query is
   infeasible. For each query the pool is the **union of the top-10 results
   from every ablation config** (dense, sparse, hybrid × rerank on/off ×
   temporal on/off = 12 configs) **plus the chunks from the articles the
   labeler picked by reading**. The pool is deduplicated by chunk id,
   **shuffled and blinded** — the grader never sees which config returned a
   chunk — and each pooled chunk is graded 2 / 1 / 0. Chunks outside the pool
   are unjudged and **count as 0** when scored. Every results file reports
   the **pool depth (10) and the mean/median pool size per query** so the
   judging budget is auditable.
3. **No self-judging.** The system's own LLM never writes labels, and
   `qwen2.5:3b` (the model under test) is never the judge (Section 3).
4. **Freeze first, run second.** `eval/queries.jsonl` (queries + grades) is
   committed before the first *scored* harness run; changes after that are
   diffs in git, visible in review.
5. **Self-consistency check, not inter-annotator agreement.** A ~15-query
   pilot is graded, set aside, and re-graded a day apart by the **same
   person** to catch ambiguous wording. The report calls this a
   self-consistency check; there is no second annotator, so no
   inter-annotator agreement is claimed anywhere.

---

## 2. Suites

### 2.1 Retrieval suite

Runs the real `POST /api/v1/search` path (or the injected `search_fn`) against
the frozen snapshot.

Metrics, macro-averaged over queries with a per-type breakdown:

| Metric | Definition |
|---|---|
| Recall@5 / Recall@10 | fraction of grade-2 chunks in the top-5 / top-10 |
| MRR@10 | reciprocal rank of the first grade-2 hit |
| nDCG@10 | graded (2/1/0) normalised cumulative gain |

Ablations (each reported as its own row, same queries):

1. dense only vs sparse only vs hybrid (RRF)
2. reranker **on** vs **off**
3. temporal ranking **on** vs **off** (default blend vs similarity-only)

Deliverable: one table where every configuration is a row — this is what
Phase 5 (ranking/eval tuning) will optimise against.

### 2.2 RAG suite — fast path and deep path scored separately

For each query, run the chat endpoint once per path (`fast`: the ≤30-word
routing; `deep`: forced) with a fixed user id and empty conversation history.

| Metric | How measured | Catches |
|---|---|---|
| Citation precision | cited `[#n]` ids that resolve to grade ≥1 chunks / all cited ids | hallucinated citations |
| Citation coverage | grade-2 chunks used in the answer / grade-2 chunks retrieved | evidence ignored (T3-deep) |
| Required-fact recall | fraction of `required_facts` present in the answer | unsupported or empty answers |
| Invalid-citation rate | citations that do not resolve to any emitted evidence item | broken `[#n]` mapping |
| Refusal-when-evidence-exists | answer refuses/hedges while ≥1 grade-2 chunk was retrieved. Detection: a **deterministic regex list committed at `eval/refusal_patterns.txt` before the first run**; the LLM judge is consulted only for borderline cases and the count of judge-routed cases is reported | **T1-deep** |
| Sub-question relevance (deep) | judge scores each planner sub-question against the user query (0/1/2) | **T3-deep hallucinated sub-question** |
| Latency p50 / p95 | wall clock, first token and full answer, **cold** (fresh process/model load) vs **warm** | regressions in token budgeting |

Cold vs warm: cold = first query after a fresh API process (BGE-M3 + reranker
load); warm = subsequent queries. p50/p95 over ≥20 runs for warm, ≥3 for cold.

### 2.3 Clustering suite

Against a **human-labelled event set**: ~30 articles hand-grouped into true
events (including at least 5 cross-source pairs covering the same story).

- Slow path: Adjusted Rand Index (ARI), Normalised Mutual Information (NMI),
  and purity over the UMAP+HDBSCAN output.
- Fast path: precision of centroid matching — of articles matched into an
  existing event, the fraction that belong there (threshold 0.72 is the knob
  this measures).

### 2.4 Content-quality suite — held-out classifier set

The Phase 1.2 numbers (P=0.984 / R=0.969) are **in-sample**: weights and
`STRONG_MARKERS` were fit on the same 102 labels. That fixture stays as a
regression guard; this suite is the honest measurement.

- Pull **~50 freshly ingested chunks** from a **new ingest** of **new URLs from
  the existing four sources only** (a fifth source is Phase 6 scope) that the
  classifier has never seen — no re-use of `boilerplate_labels.jsonl` rows, no
  marker design informed by these labels. Stratify with **≥15 TechCrunch and
  ≥15 BBC chunks**, since those were the junk-heavy sources.
- Two-pass **self-consistency** labelling (label → cool-off → relabel by the
  same person; disagreements resolved and reported). This is not
  inter-annotator agreement and is not reported as such.
- Report **precision and recall with 95% Wilson intervals** — at n≈50 the
  intervals are wide and the doc must show them, not hide them.
- Trigger for revisiting the threshold: held-out precision < 0.95.

---

## 3. Judging

**Deterministic (no LLM, always preferred):**

- id resolution (citation → chunk/article, sub-question → retrieval ids),
- `required_facts` substring/regex match against the answer,
- latency, token counts, retrieval metrics over graded labels,
- clustering ARI/NMI/purity against the labelled event set,
- classifier precision/recall against held-out labels.

**LLM-judged (only where text cannot be matched):** sub-question relevance,
answer-vs-evidence entailment, citation-coverage judgement.

- **Judge model:** local Ollama only, no paid API. Start with
  **`qwen2.5:14b`**; if a single judgment takes **> ~20 s** on this machine,
  fall back to **`qwen2.5:7b`**. Either way the **judge model and its version
  are recorded in every results file**. The judge is always a model *other
  than* the one under test (`qwen2.5:3b`); κ decides whether we trust it.
- **Prompt:** a single frozen rubric prompt, stored at
  `eval/prompts/judge.md`, returning strict JSON
  `{"score": 0|1|2, "reason": "..."}`. Never edited in place — a prompt change
  is a new judge version and requires re-baselining.
- **Human agreement check:** 20% of LLM-judged items are also labelled by a
  human; report raw agreement and Cohen's κ. κ ≥ 0.70 is the gate for trusting
  the judge, otherwise the rubric is revised and the sample re-labelled.

---

## 4. Runner

```bash
uv run python -m eval.run --suite retrieval   # metrics + ablations
uv run python -m eval.run --suite rag         # fast + deep, all RAG metrics
uv run python -m eval.run --suite clustering
uv run python -m eval.run --suite quality     # held-out classifier set
uv run python -m eval.run --suite all
```

Flags: `--snapshot <id>`, `--queries eval/queries.jsonl`, `--limit N`,
`--mock` (no Ollama/Qdrant network calls — see CI), `--baseline <path>`.

Output:

- `eval/results/<yyyymmdd>_<gitsha7>.json` — machine-readable: snapshot id,
  query file hash, git sha, per-query records, aggregate metrics, judge
  version, environment (model names, `CHAT_NUM_CTX`).
- `eval/results/<yyyymmdd>_<gitsha7>.md` — the summary tables a human reads.
- `eval/results/latest.json` — pointer copy for the CI comparison step.

The JSON embeds the snapshot id and query-file hash so a result can never be
compared against a dataset it was not produced on.

---

## 5. CI

- A **committed mocked subset**: **12 queries covering all four query types
  (fact / compare / multi-hop / time-bound) and both paths (fast and deep)**,
  with a deterministic fake retriever and canned LLM responses (no Ollama, no
  network, no Qdrant). Its purpose is to test the **harness arithmetic and
  wiring** — the same code path as the full suite, fixtures in
  `tests/fixtures/eval/`.
- **CI numbers are not quality numbers.** With a fake retriever and canned
  output every metric is deterministic, so the CI gate asserts **EXACT
  equality** against the committed baseline `eval/results/baseline_ci.json` —
  any deviation (up or down) fails the job, because it means the harness
  changed, not that quality changed. Baseline updates happen only by
  committing a new baseline file in their own reviewed commit.
- The **full suite runs locally** (needs Docker + Ollama) and is never a CI
  gate in Phase 2 — quality numbers come from the local runs only.

---

## 6. Exit criteria

Phase 2 is **done** when all of the following hold — no quality targets are
set in Phase 2; targets come later, measured against this baseline:

1. The full suite runs **end to end on snapshot #1** (`retrieval`, `rag`,
   `clustering`, `quality`) with no manual steps.
2. The **baseline results (JSON + markdown) are committed**.
3. The two known deep-path failures **show up as numbers**: T1-deep appears in
   refusal-when-evidence-exists, and T3-deep appears in sub-question relevance
   (and citation coverage). A defect that cannot be measured is a blocker for
   exit.
4. Coverage stays ≥ 80% and CI is green (exact-equality harness gate).

**Rules:** no pipeline or model changes in Phase 2 — the deep-path defects are
*measured*, not fixed. Milestone reports (each with commit SHA + CI link):
(a) snapshot + query set ready for Yaswanth's 20% review, **before freezing**;
(b) retrieval suite + ablation table; (c) RAG suite; (d) final baseline.

---

## 7. Effort & order of work

| # | Work item | Est. |
|---|---|---|
| 1 | Snapshot writer + `eval/queries.jsonl` schema/validation, docs | 0.5 d |
| 2 | Labelling: 60–100 queries, relevance grades, double-pass pilot | 1.5–2 d (human-heavy) |
| 3 | Retrieval suite + ablation matrix | 1 d |
| 4 | RAG suite (fast/deep), latency harness with cold/warm control | 1.5 d |
| 5 | Clustering labelled event set + metrics | 1 d |
| 6 | Held-out quality set (new ingest) + Wilson interval reporting | 1 d (plus ingest timing) |
| 7 | Judge prompt + human agreement pass (κ report) | 0.5–1 d |
| 8 | CI mocked subset, exact-equality baseline gate | 0.5–1 d |
| | **Total** | **~7–9 days** |

Order: 1 → 3 (fastest signal) → 4 → 2 (runs in parallel, human-bound) →
5 → 6 → 7 → 8. Baseline is cut after 3+4 pass on the frozen snapshot.

## 8. Decisions (resolved 2026-10-11, by Yaswanth)

1. **Judge:** local Ollama model, no paid API. Try `qwen2.5:14b`; if it exceeds
   ~20 s per judgment on this machine, use `qwen2.5:7b`. Record judge model +
   version in every results file. The κ ≥ 0.70 gate decides whether we trust
   it.
2. **Held-out quality set:** new URLs from the **existing four sources only**;
   a fifth source is Phase 6 scope. At least 15 TechCrunch and 15 BBC chunks
   (the junk-heavy sources).
3. **CI subset:** 12 queries is fine, provided they cover all four query types
   and both paths (fast and deep).
4. **Labelling:** pooling as specified in §1.3; the coding agent drafts, and a
   random 20% review by Yaswanth gates the freeze.
5. **CI gate:** exact equality with the committed mocked baseline — CI numbers
   test the harness, not quality.
