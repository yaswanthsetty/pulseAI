# PulseAI

Real-time AI news intelligence platform. Ingests global news, embeds for semantic
search, clusters into evolving events, and answers questions with cited sources.

> **Backend docs:** [`DEVELOPER.md`](DEVELOPER.md) — architecture, setup,
> config, schema, workflows, testing, deployment, troubleshooting.
> **Frontend docs:** [`frontend/README.md`](frontend/README.md) — design system,
> pages, architecture.

## What it does

1. **Ingests** — polls RSS feeds, deduplicates (exact + fuzzy), classifies by
   category/country/language, stores article bodies out-of-line.
2. **Embeds** — chunks articles into sentence-aligned passages, encodes with
   BGE-M3 (dense + sparse vectors), stores in Qdrant.
3. **Searches** — semantic / keyword / hybrid retrieval, cross-encoder reranking,
   intent-aware temporal ranking with freshness decay.
4. **Clusters** — fast centroid-match on every new article, scheduled UMAP +
   HDBSCAN for new stories, automatic closure of stale events.
5. **Chats** — RAG pipeline with SSE streaming, fast-path (single retrieve →
   generate → cite) and deep-path (planner → retriever × N → reasoner × N →
   synthesizer), evidence agreement scoring.
6. **Reports** — executive intelligence reports with source analysis.

## Quick start

**Docker (all-in-one):**

```bash
docker compose up --build -d
docker compose exec api uv run alembic upgrade head
curl http://localhost:8000/readyz   # → {"status": "ready", ...}
```

**Local development:**

```bash
docker compose up -d postgres qdrant redis   # infrastructure
cp .env.example .env
uv sync && uv run alembic upgrade head

# Three terminals:
uv run pulseai-api          # http://localhost:8000 (Swagger at /docs)
uv run pulseai-scheduler    # per-source polling
uv run pulseai-worker       # RQ job executor
```

**Frontend:**

```bash
cd frontend && npm install && npm run dev   # http://localhost:3000
```

## Tech stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.14, FastAPI, uvicorn |
| Database | PostgreSQL 15, Redis 7 + RQ, Qdrant |
| ML | BGE-M3 embeddings, BGE-reranker, UMAP + HDBSCAN |
| LLM | Ollama (qwen2.5:3b) for summaries and chat |
| Frontend | Next.js 16, TypeScript, Tailwind CSS v4 |
| Auth | bcrypt, PyJWT, Clerk/Auth0 (optional) |
| CI | GitHub Actions — ruff, import-linter, pytest (80% coverage) |

## Architecture

```
RSS feeds → Scheduler → Worker → PostgreSQL + Qdrant
                                     ↓
                              API (FastAPI) ← Frontend (Next.js)
                                     ↓
                              Chat (SSE) + Reports (LLM)
```

**Modular monolith:** strictly-bounded modules under one FastAPI app.
`modules/api` is the only top layer; sibling imports are rejected by
import-linter in CI. Three processes share the codebase: `api` (HTTP),
`scheduler` (polling), `worker` (RQ jobs).

## API overview

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/search` | Semantic / keyword / hybrid search |
| `GET /api/v1/events` | Paginated event feed |
| `GET /api/v1/events/{id}/timeline` | Articles grouped by day |
| `POST /api/v1/chat` | RAG chat with SSE streaming |
| `POST /api/v1/reports/generate` | Executive report generation |
| `POST /api/v1/auth/login` | JWT authentication |

Full API reference: [`DEVELOPER.md`](#8-api) or interactive docs at `/docs`.

## Authentication

- **Local:** register/login with bcrypt passwords, HS256 JWTs.
- **Managed:** Clerk or Auth0 via RS256 JWT verification.
- **API keys:** `pls_`-prefixed, scoped (`read`/`chat`/`reports`).
- **RBAC:** `user < analyst < admin` — enforced per route, integration-tested.

## Quality gates

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
uv run lint-imports        # module boundaries
uv run pytest              # 305 tests (need docker infra up)
```

## Project status

See [`PROJECT_STATUS_AND_ROADMAP.md`](PROJECT_STATUS_AND_ROADMAP.md) for
completion status and roadmap.
