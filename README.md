# PulseAI

Real-time AI news intelligence platform — a modular-monolith backend that ingests
global news streams, deduplicates and classifies articles, and prepares them for
temporal retrieval and event-centric intelligence (spec v2.0, Phase 1 complete).

## Architecture

```
 News sources (RSS)                 ┌─────────────────────────────┐
        │                           │  FastAPI application         │
        ▼                           │  modules/api (routers)       │
 ┌─────────────┐   enqueue          │  modules/ingestion           │
 │  scheduler  ├──────────────────► │  modules/retrieval (P2)      │
 └─────────────┘    poll jobs       │  modules/ranking    (P4)     │
        ▲                           │  modules/events      (P3)    │
        │ retry markers (Redis)     │  modules/agents      (P5)    │
 ┌─────────────┐                    │  modules/auth        (1.5)   │
 │ RQ worker   │ ◄── ingest queue   │  modules/reports     (P5)    │
 └──────┬──────┘                    └────────────┬────────────────┘
        │                                        │
        ▼                                        ▼
 PostgreSQL ───────────► object storage ◄─── previews stay inline
 (metadata)             (full article bodies)
```

- **Modular monolith** (spec §8): strictly-bounded modules; `modules/api` is the
  only top layer; sibling imports are rejected by **import-linter** in CI.
- **Decoupled processes:** `api` (HTTP), `scheduler` (per-source polling, FR-1),
  and `worker` (RQ, executes poll/process jobs). Backoff retries (FR-3) are
  driven by Redis TTL markers, so the same code runs on Linux and Windows.
- **Object storage:** full article bodies live out-of-line (`content_ref`);
  Postgres keeps metadata + a ~500-char preview (spec §10/§31).

## Tech stack

Python 3.14 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 15 · Redis 7 + RQ ·
Qdrant (vector store, used from Phase 2) · BeautifulSoup + feedparser · langdetect
· ruff + pytest + import-linter.

## Quick start (Docker)

```bash
docker compose up --build -d        # postgres, qdrant, redis, api, worker, scheduler
docker compose exec api uv run alembic upgrade head
curl http://localhost:8000/readyz   # → {"status": "ready", ...}
curl http://localhost:8000/api/v1/sources
```

## Quick start (local development)

Prerequisites: Docker (for Postgres/Qdrant/Redis) and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Infrastructure (Postgres on :5434, Qdrant on :6333, Redis on :6379)
docker compose up -d postgres qdrant redis

# 2. Environment
cp .env.example .env                # adjust if needed

# 3. Dependencies + schema
uv sync
uv run alembic upgrade head

# 4. Run the three processes (three terminals)
uv run pulseai-api                  # http://localhost:8000 (Swagger at /docs)
uv run pulseai-scheduler            # per-source polling (FR-1)
uv run pulseai-worker               # RQ job executor
```

The API seeds three demo RSS sources on startup (`SEED_DEFAULT_SOURCES=true`);
the scheduler polls them and the worker processes articles end to end
(dedupe → clean → classify → store).

## API surface (Phase 1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz`, `/readyz`, `/health` | liveness / readiness / legacy alias |
| GET | `/api/v1/sources` | list sources with health |
| POST | `/api/v1/sources` | add source (feed validated first, FR-4) |
| PATCH | `/api/v1/sources/{id}` | update credibility / interval / status |
| POST | `/api/v1/sources/{id}/poll` | queue an immediate poll |
| GET | `/api/v1/articles` | list/filter articles (date, source, category, …) |
| GET | `/api/v1/articles/{id}` | article detail |
| POST | `/api/v1/search` | semantic search over Qdrant (early Phase 2 surface; returns `[]` until vectors are populated) |

All errors use the spec §19 envelope `{"error": {"code", "message", "request_id"}}`;
all lists use `{items, page, page_size, total}`.

## Quality gates

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
uv run lint-imports        # module boundaries (spec §8)
uv run pytest              # unit + integration tests (need docker infra up)
```

CI (GitHub Actions) runs all four gates plus `alembic upgrade head` on a fresh
database.

## Project status

- **Phase 1 (core infra + ingestion) — complete:** modular monolith, full spec
  §10 schema (18 tables + Alembic migrations), per-source polling (FR-1),
  URL-hash + fuzzy dedupe (FR-2), backoff retries + degraded status (FR-3),
  feed validation (FR-4), HTML→text + metadata extraction (FR-5), language
  detection (FR-6), taxonomy classification (FR-7), object storage, health
  endpoints, Docker, CI, tests.
- **Early Phase 2 surface:** `POST /api/v1/search` (BGE-small dense vectors in
  Qdrant, cosine) is ported into `modules/retrieval`; the embedding pipeline that
  populates vectors lands with Phase 2. The model and Qdrant client load lazily,
  so API startup never downloads a model.
- **Postgres driver:** sync `psycopg2` by default; set
  `POSTGRES_DRIVER=postgresql+asyncpg` for the async driver option (Phase 2
  async work). The sync engine and Alembic always strip the async prefix.
- **Next:** Phase 1.5 authentication (Clerk/Auth0 + RBAC), then Phase 2
  embeddings (BGE-M3 + chunking + Qdrant). See `PROJECT_STATUS_AND_ROADMAP.md`.
