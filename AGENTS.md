# PulseAI - Agent Learnings

## Read this first
This document tracks critical learnings, rules, and architectural context discovered by agents working on PulseAI. Always refer to this document before writing code.

## Current state
PulseAI is an advanced web-app running FastAPI, Qdrant, Postgres, and Redis. It has a modular monolith architecture, strict import contracts, and connects to an Ollama LLM setup.

## Upgrade plan status
- **Phase 1 (Ground every answer in real text):** DONE.
- **Phase 2 (Evaluation harness):** Pending.
- **Phase 3 (Improve chunking & ranking):** Pending.
- **Phase 4 (Agent tools & tool calling):** Pending.

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
- 305 backend tests, all need Docker (Postgres :5434, Qdrant :6333, Redis :6379).

**Common Issues**
- Port 8000 is reserved by Hyper-V on Windows - use 8090 instead.
- `ruff check --fix` auto-fixes import ordering and type annotation style.
- Migration files must be manually written (not `--autogenerate`).
- The `api/router.py` import order matters for ruff.
- **Context Budgeting**: Always compute Ollama token budgets dynamically. Use `chat_num_ctx` minus prompt size minus `chat_num_predict` minus 10% safety margin. Do not hardcode 3000 tokens.

## Key file map
- `backend/core/config.py`: Environment configuration and Pydantic settings.
- `backend/core/counters.py`: In-memory thread-safe metric counters (Phase 1).
- `backend/modules/agents/grounding.py`: Pure functions for context budgeting and citation mapping (Phase 1).
- `tests/unit/test_grounding.py`: Unit tests for token calculations.

## Next steps
- Plan and implement Phase 2 (Evaluation harness).