# PulseAI - Agent Learnings

## Read this first
This document tracks critical learnings, rules, and architectural context discovered by agents working on PulseAI. Always refer to this document before writing code.

## Current state
PulseAI is an advanced web-app running FastAPI, Qdrant, Postgres, and Redis. It has a modular monolith architecture, strict import contracts, and connects to an Ollama LLM setup.

**The "agents" are one fixed pipeline on one Ollama model.** The deep path is a fixed planner -> retriever -> reasoner -> synthesizer sequence, all steps calling `qwen2.5:3b`. There are no independent agents, no tool calling, and no planner that can adapt its strategy (Phase 4 territory). Embeddings are `BAAI/bge-m3` (dense + sparse); reranking is `BAAI/bge-reranker-base` via sentence-transformers `CrossEncoder`. Ranking blend defaults: **0.55 semantic + 0.20 freshness + 0.15 credibility + 0.10 event** (0.35/0.40/0.15/0.10 for `recency`, 0.70/0.05/0.15/0.10 for `historical`, stored in `ranking_configs`).

## Upgrade plan status
Authoritative phase list (order matters; do not renumber):
- **Phase 1 — Ground every answer in real text:** DONE.
  - 1.1 grounding pipeline (context budgeting, citation sanitisation, evidence agreement): DONE.
  - 1.2 chunk-filter refit, trafilatura extraction, safe re-ingest CLI, corpus cleanup: DONE (CI green on `c3fc039`).
- **Phase 2 — Evaluation harness:** PLAN WRITTEN, awaiting approval (`docs/plans/phase2_eval.md`). No code until approved.
- **Phase 3 — NLI verification:** Pending.
- **Phase 4 — Real tool-using agent loop:** Pending.
- **Phase 5 — Ranking/eval tuning:** Pending.
- **Phase 6 — Multilingual / data breadth:** Pending.
- **Phase 7 — Product polish + frontend tests:** Pending.
- **Phase 8 — Deploy / OTel / load:** Pending.
- **Phase 9 — README:** Pending.

## Ingestion & extraction (Phase 1.2)
- **Pipeline order:** trafilatura (`favor_precision`) -> hardened BeautifulSoup fallback -> chunk filter (`is_boilerplate`) -> chunker -> embed. A chunk rejected by the classifier never reaches Qdrant.
- **Extractor tracking:** every processed article records `articles.extractor` (`trafilatura` / `fallback` / `summary`) and `articles.extractor_version` (migration `c9f4a2e71b38`), so a future extraction upgrade can target only stale rows.
- **Re-ingest CLI:** `uv run pulseai-reingest --dry-run` prints per-article diffs and writes nothing; `--limit N` caps the run. It re-fetches each article, re-extracts, re-chunks, and replaces vectors embed-then-swap (new vectors upsert before any delete; a failed embed rolls back and keeps the old version). Fetch failure keeps the stored text. One article's crash never aborts the run.
- **Prune rule (only measured deletion path):** old vectors are deleted only when the re-fetch yields no prose AND every old chunk is classifier-confirmed junk; the article then converges to `summary`/`low` (the state a fresh ingest of that URL produces). Anything less keeps the old vectors.
- **trafilatura gotcha:** 2.3.1's `deduplicate=True` uses a *process-global* LRU — the n-th identical HTML document silently returns `None` and the parser silently falls back. `parser.py` scopes the cache per call; do not "simplify" that away.

## Decisions
- Token Context Budgeting: Replaced hardcoded context limits with dynamic calculations based on `CHAT_NUM_CTX` and `CHAT_NUM_PREDICT`.
- Truncation: Citations use `[#n]` syntax and are sanitised dynamically to drop hallucinatory markers.

## Architecture rules
- Modular monolith: business modules (retrieval, ranking, events, agents) cannot import each other. The router layer (`api/router.py`, `agents/router.py`) is the integration point that imports from siblings.
- `.importlinter` enforces 4 contracts. Run `uv run lint-imports` to verify.
- `agents/service.py` must NOT import from `retrieval` - the router injects `search_fn` as a callable.

## Models
- `qwen2.5:3b` is the default LLM (fits in ~2GB RAM). `qwen3.5:9b` needs 5.4GB and fails on machines with <6GB free.
- Qwen 3.5 is a thinking model - always pass `"think": false` to Ollama or it eats all tokens on internal reasoning.
- BGE-M3 embedding model loads lazily on first search call (~30s cold start). Subsequent calls are fast.
- Cross-encoder reranker (BGE-reranker-base) uses sentence-transformers `CrossEncoder`, NOT `FlagReranker`.

## Backend / Frontend / Testing / Common issues

**Backend**
- `blend_scores()` returns `list[tuple[SearchResult, dict]]`, not `list[SearchResult]`.
- Search endpoint is a sync `def` (not `async def`) - FastAPI runs it in a threadpool to avoid blocking the event loop.
- `generate_report` uses `asyncio.run()` inside a sync endpoint.
- `_persist_message` and `_log_usage` swallow exceptions (non-critical paths that run after response is streamed).
- SSE format: `_sse_line()` produces `"data: " + json.dumps(data) + "\n\n"`.

**Frontend**
- Tailwind v4 uses CSS-based config (`@theme inline` in `globals.css`), not `tailwind.config.js`.
- Auth token stored in `localStorage` (key: `pulseai_token`). Check `typeof window === "undefined"`.
- SSE parsing splits on `"\n\n"` - backend uses LF-only (`chr(10)+chr(10)`).
- Template literals with `${}` break in bash heredocs - use Python or `cat > file << 'EOF'` (quoted delimiter).

**Testing**
- Tests use `conftest.py` `_MUTABLE_TABLES` to truncate between tests. New tables must be added there.
- Mock Ollama in tests - never hit the real model. Use `patch("backend.modules.agents.service.httpx.AsyncClient")` for streaming, `patch("backend.modules.agents.service._call_ollama_blocking")` for deep path.
- **386 backend tests**, all need Docker (Postgres, Qdrant :6333, Redis :6379). Coverage **80.87%** against the enforced 80% gate — verified 2026-10-11 at `c3fc039` (`uv run pytest -q --cov=backend`). Rerun before quoting a test count; update this line when it changes.
- `tests/conftest.py` honors `.env`'s `POSTGRES_PORT` (currently 5540): Hyper-V moves the usable host-port ranges across reboots, so the Postgres host port is not a fixed constant.

**Common Issues**
- **Windows Hyper-V reserved port ranges**: Hyper-V reserves *shifting* TCP ranges on Windows (e.g. 8069-8168, 8169-8268), and which ranges are reserved changes across reboots. A port that worked yesterday can be unbindable today (`WinError 10013`). Check first with `netsh interface ipv4 show excludedportrange protocol=tcp` and pick a port outside every listed range. This affects both the API (`APP_PORT`, default 8000) and Ollama (`OLLAMA_URL`, default `http://localhost:11434` - on this host Ollama serves on port 16000 instead). Both are env-configurable; never assume 8090 is free either.
- `ruff check --fix` auto-fixes import ordering and type annotation style.
- Migration files must be manually written (not `--autogenerate`).
- The `api/router.py` import order matters for ruff.
- **Context Budgeting**: Always compute Ollama token budgets dynamically. Use `chat_num_ctx` minus prompt size minus `chat_num_predict` minus 10% safety margin. Do not hardcode 3000 tokens.

## KNOWN LIMITATIONS
- **Evidence agreement is not verification.** `_compute_agreement()` (agents/service.py) is title-token Jaccard >= 0.10 between cited sources. It measures lexical title overlap only; it does NOT check that any claim is entailed by the evidence. Phase 3 (NLI verification) addresses this.
- **Deep planner can invent off-topic sub-questions.** In the Phase 1.2 smoke reruns, the planner emitted a sub-question about an entity that was not in the query; retrieval then faithfully matched it and the final answer went off-topic (T3-deep). Fix target: Phase 2 metric "sub-question relevance", Phase 4 planner redesign.
- **Synthesizer can refuse despite strong evidence.** T1-deep refused even when clean, high-scoring article prose was retrieved — the refusal is a downstream reasoner/synthesizer behavior, not a corpus-quality problem.
- **Classifier metrics are in-sample.** P=0.984 / R=0.969 are measured on the same 102 labels the weights and `STRONG_MARKERS` were fit on. Treat `tests/fixtures/boilerplate_labels.jsonl` + its test as a regression guard, NOT as validated accuracy. An honest held-out measurement (freshly labelled chunks from a new ingest) is Phase 2 work.
- **5 articles are summary-only** (`extractor=summary`, `extraction_quality=low`): body-less listing/video pages where neither trafilatura nor the fallback finds prose. Their Qdrant vectors were pruned; they serve description text only.
- **Dependency audit exceptions:** `accelerate` (PYSEC-2026-3804, no fix released, needed by BGE-M3) and `braces` (dev dependency) are ignored in `.github/workflows/ci.yml`. **Revisit 2027-01-15** — drop each ignore once a patched release exists.
- **No deployment, no OTel.** Local Docker Compose only; no production deployment and no OpenTelemetry tracing exist yet (Phase 8).

## Key file map
- `backend/core/config.py`: Environment configuration and Pydantic settings.
- `backend/core/counters.py`: In-memory thread-safe metric counters (Phase 1).
- `backend/core/content_quality.py`: Boilerplate chunk classifier + window-based whole-body quality gate (Phase 1.2).
- `backend/modules/agents/grounding.py`: Pure functions for context budgeting and citation mapping (Phase 1).
- `backend/workers/reingest.py`: Safe re-extraction CLI `pulseai-reingest` (Phase 1.2).
- `tests/fixtures/boilerplate_labels.jsonl`: 102-row hand-labelled classifier regression fixture.
- `tests/unit/test_grounding.py`: Unit tests for token calculations.
- `docs/plans/phase2_eval.md`: Evaluation-harness plan (Phase 2, awaiting approval).

## Git rules
- **Never force-push `main`.** Fix forward with new commits.
- Do NOT add "Generated with Codebuff" / Codebuff co-author trailers to commit messages: the Freebuff client amends such commits after creation to strip the trailer, which rewrites the SHA and forces a force-push to update the remote (this is what rewrote `a0947e5`/`f44a37f` -> `f11746d`/`c3fc039`).
- The guards are version-controlled in **`.githooks/`**: `commit-msg` strips those trailer lines before the commit object exists, and `pre-push` rejects any non-fast-forward push to `main`. **One-time setup per clone:** `git config core.hooksPath .githooks` (already set in this working copy; git ignores `.git/hooks` once `core.hooksPath` is set).

## Next steps
- Phase 2 (evaluation harness): get `docs/plans/phase2_eval.md` approved, then implement in the order given there. Do not start Phase 2 code before approval.