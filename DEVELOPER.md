# PulseAI — Developer Documentation

**Living document.** Keep this file synchronized with the codebase: whenever you
add, change, or remove behavior, update the relevant section here. Verify against
the code — do not document assumptions.

Related documents: [`README.md`](README.md) (overview + quick start),
[`PROJECT_STATUS_AND_ROADMAP.md`](PROJECT_STATUS_AND_ROADMAP.md) (completion
status and roadmap), [`frontend/README.md`](frontend/README.md) (frontend).

## Table of contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Tech stack](#3-tech-stack)
4. [Repository layout](#4-repository-layout)
5. [Setup and local development](#5-setup-and-local-development)
6. [Configuration](#6-configuration)
7. [Database](#7-database)
8. [API](#8-api)
9. [Authentication and authorization](#9-authentication-and-authorization)
10. [Key workflows](#10-key-workflows)
11. [Frontend](#11-frontend)
12. [Testing](#12-testing)
13. [Deployment](#13-deployment)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. Overview

PulseAI is a real-time AI news intelligence platform. It ingests global news
streams over RSS, deduplicates and classifies articles, embeds them for semantic
search, and groups duplicate coverage into **events** (clusters of articles about
the same story). The backend is a **modular monolith**: strictly bounded modules
under one application, so it stays deployable as a single service while each
concern stays independent.

What exists today:

- **Ingestion** — scheduled RSS polling, dedupe (exact URL + fuzzy title), HTML
  cleaning, language detection, category classification, body storage.
- **Authentication** — local or managed-provider (Clerk/Auth0) logins, role-based
  access control, API keys, rate limiting, CSRF, audit logging.
- **Retrieval** — sentence-aware chunking, BGE-M3 embeddings (dense + sparse),
  semantic / keyword / hybrid search with filters, cross-encoder reranking.
- **Events** — incremental clustering: fast centroid-match path, scheduled UMAP
  + HDBSCAN slow path, automatic closure, keyword search, merge API, notification
  rules, abstractive LLM summaries via Ollama.
- **Ranking** — intent-aware temporal ranking with freshness decay, credibility
  scoring, and event signal blending.
- **Agents** — fast-path SSE chat with evidence citations, deep-path multi-step
  reasoning with evidence agreement scoring, executive report generation.
- **Frontend** — Next.js 16 app with Kimi-inspired dark UI: search, events, chat,
  reports, admin panel, settings, command palette.

The product specification lives in `tempdocs/PulseAI_Technical_Specification_v2.md`;
its section numbers are referenced throughout the code.

## 2. Architecture

```
 News sources (RSS)                 ┌─────────────────────────────┐
        │                           │  FastAPI application         │
        ▼                           │  modules/api (routers)       │
 ┌─────────────┐   enqueue          │  modules/ingestion           │
 │  scheduler  ├──────────────────► │  modules/retrieval           │
 └─────────────┘    poll jobs       │  modules/events              │
        ▲                           │  modules/auth                │
        │ retry markers (Redis)     │  modules/ranking             │
 ┌─────────────┐                    │  modules/agents              │
 │ RQ worker   │ ◄── 3 queues       │  modules/reports             │
 └──────┬──────┘  ingest/embed/     └────────────┬────────────────┘
        │            cluster                     │
        ▼                                        ▼
 PostgreSQL ───────────► object storage ◄─── previews stay inline
 (metadata)             (full article bodies)
        └────► Qdrant (chunk vectors + event centroids) ◄──── search

 Ollama (Qwen 2.5:3b) ◄── chat + abstractive summaries
 Next.js Frontend ◄────── FastAPI API
```

**Processes** (same codebase, different entrypoints):

| Process | Entrypoint | Job |
|---|---|---|
| API | `pulseai-api` | FastAPI HTTP server (`:8090`), routers, error envelope |
| Scheduler | `pulseai-scheduler` | Every `scheduler_tick_seconds` (30s default): enqueue due source polls, backoff retries, periodic embedding reconcile + event slow-path/closure |
| Worker | `pulseai-worker` | RQ worker consuming the `ingest`, `embed`, and `cluster` queues |
| One-shot CLIs | `pulseai-backfill-embeddings` / `pulseai-backfill-clusters` | Manual whole-corpus backfills / reconciles |

**Module boundaries** (enforced by import-linter, `.importlinter`):

1. `modules/api` is the top layer — nothing else imports it.
2. Business modules (`retrieval`, `ranking`, `events`, `agents`, `reports`,
   `ingestion`) never import each other. Cross-module job hand-offs go through
   the `core.queue` hub.
3. `modules/auth` is shared cross-cutting infrastructure — business modules may
   import it; it never imports them.

## 3. Tech stack

| Concern | Choice |
|---|---|
| Runtime | Python ≥ 3.14 |
| Web framework | FastAPI + uvicorn |
| ORM / migrations | SQLAlchemy 2.0 + Alembic |
| Data stores | PostgreSQL 15, Redis 7 + RQ, Qdrant |
| Embeddings | BGE-M3 (dense + sparse), BGE-reranker (cross-encoder) |
| Clustering | UMAP + HDBSCAN |
| LLM | Ollama (Qwen 2.5:3b) |
| Frontend | Next.js 16, React 19, Tailwind CSS v4, TanStack Query |
| Auth | bcrypt, PyJWT, RBAC |
| CI | GitHub Actions — ruff, import-linter, pytest |

**Console scripts:**

```bash
uv run pulseai-api                   # API server
uv run pulseai-worker                # RQ worker
uv run pulseai-scheduler             # poll scheduler + periodic reconciles
uv run pulseai-backfill-embeddings [--recreate]
uv run pulseai-backfill-clusters
```

## 4. Repository layout

```
backend/
  main.py                 # FastAPI app: lifespan (seed), CORS, routers, error envelope
  core/                   # cross-cutting infrastructure
    config.py             #   all settings (pydantic-settings)
    database.py           #   engine, SessionLocal, get_db, session_scope
    queue.py              #   Redis/RQ: queues, locks, markers, job enqueuers
    storage.py            #   object storage (local / S3)
    logging.py            #   JSON structured logging
    ssrf.py               #   SSRF protection
    audit.py              #   audit_log writer
    pagination.py         #   list pagination envelope
  db/
    models.py             # all 21 tables (SQLAlchemy 2 mapped_column style)
    seed.py / seed_data.py  # idempotent reference-data seeding
  modules/
    api/                  # health.py + router.py (aggregate /api/v1, global rate-limit dep)
    auth/                 # router, service, security, deps (RBAC), ratelimit, csrf, schemas
    ingestion/            # service, jobs, parser, fetcher, classifier, dedupe, router, schemas, seeds
    retrieval/            # service (embed + search + rerank), chunker, jobs, router, schemas
    events/               # service (fast path, slow path, closure, notifications), router, schemas
    ranking/              # service (intent detection, temporal ranking), schemas
    agents/               # service (chat SSE, deep path, reports), router, schemas
    reports/              # placeholder (reports logic is in agents module)
  workers/
    worker.py             # RQ worker (SimpleWorker on Windows)
    scheduler.py          # poll scheduling + periodic reconciles
    backfill.py           # one-shot CLIs + periodic reconcile twins
frontend/
  src/
    app/                  # 9 pages (Next.js App Router)
    components/           # Shell, AuthGuard, ErrorBoundary, Toast, CommandPalette
    lib/                  # api.ts (backend client), utils.ts (cn helper)
migrations/               # Alembic versions
tests/                    # unit + integration tests
docker-compose.yml / Dockerfile / .github/workflows/ci.yml
```

## 5. Setup and local development

Prerequisites: **Docker**, **uv** (Python ≥ 3.14), **Node.js** 18+, **Ollama**.

```bash
# 1. Infrastructure
docker compose up -d postgres qdrant redis

# 2. Backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090

# 3. Seed articles and create events (if needed)
uv run pulseai-backfill-embeddings
uv run pulseai-backfill-clusters

# 4. Frontend
cd frontend
npm install
npm run dev

# 5. Ollama (for chat + summaries)
ollama serve
```

**Model downloads (lazy):** BGE-M3 (~2.3 GB) and BGE-reranker-base (~1.1 GB)
download on first use and cache under `~/.cache/huggingface`.

## 6. Configuration

All settings in `backend/core/config.py` (pydantic-settings). See `.env.example`.

| Group | Key variables |
|---|---|
| Postgres | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (required) |
| Qdrant | `QDRANT_URL` (default `http://localhost:6333`) |
| Redis | `REDIS_URL` (default `redis://localhost:6379/0`) |
| Auth | `AUTH_PROVIDER` (none/clerk/auth0), `JWT_SECRET`, `JWT_ACCESS_TTL_MINUTES` (15) |
| Rate limit | `RATE_LIMIT_ANON_PER_MINUTE` (30), `RATE_LIMIT_AUTH_PER_MINUTE` (120) |
| Embeddings | `EMBEDDING_MODEL` (BAAI/bge-m3), chunking params (256/40/300) |
| Rerank | `RERANKER_MODEL` (BAAI/bge-reranker-base), `RERANK_TOP_K` (50), `RERANK_TOP_N` (10) |
| Events | `EVENT_MATCH_THRESHOLD` (0.72), `EVENT_CLOSE_HOURS` (72) |
| LLM | `OLLAMA_URL`, `CHAT_MODEL` (qwen2.5:3b), `SUMMARY_MODEL` |
| Ingestion | `SEED_DEFAULT_SOURCES` (true), polling intervals, dedupe thresholds |

## 7. Database

21 tables in `backend/db/models.py`. Groups:

| Group | Tables |
|---|---|
| Lookup (seeded) | `categories`, `countries`, `languages`, `ranking_configs` |
| Ingestion | `sources`, `articles`, `article_chunks` |
| Events | `events`, `event_articles` |
| Identity | `users`, `api_keys`, `refresh_tokens` |
| Chat/Reports | `conversations`, `conversation_messages`, `reports`, `llm_usage` |
| User content | `saved_reports`, `saved_searches`, `bookmarks`, `notification_rules` |
| Ops | `audit_log` |

Schema changes: edit `models.py`, then
`uv run alembic revision --autogenerate -m "..."`, review, apply.

## 8. API

All responses use the error envelope
`{"error": {"code", "message", "request_id"}}`; all lists use
`{items, page, page_size, total}`. Interactive docs: `/docs`.

### Auth

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/auth/register` | Create account (role `user`) | open |
| POST | `/api/v1/auth/login` | Access token + refresh cookie | open |
| POST | `/api/v1/auth/session` | Exchange provider JWT for tokens | open |
| POST | `/api/v1/auth/refresh` | Rotate refresh token | cookie |
| POST | `/api/v1/auth/logout` | Revoke refresh, clear cookies | cookie |

### Users & API Keys

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/users/me` | Current profile + role | user+ |
| GET | `/api/v1/users` | List users | admin |
| PATCH | `/api/v1/users/{id}/role` | Change role (audited) | admin |
| GET/POST | `/api/v1/api-keys` | List / create (raw returned once) | user+ |
| DELETE | `/api/v1/api-keys/{id}` | Revoke | user+ |
| GET | `/api/v1/usage` | Token usage summary | user+ |

### Ingestion

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/sources` | List sources with health | user+ |
| POST | `/api/v1/sources` | Add source (feed validated) | admin |
| PATCH | `/api/v1/sources/{id}` | Update source settings | admin |
| POST | `/api/v1/sources/{id}/poll` | Queue immediate poll | admin |
| GET | `/api/v1/articles` | List/filter articles | open |
| GET | `/api/v1/articles/{id}` | Article detail | open |

### Search

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/search` | Semantic/keyword/hybrid search with filters | open |

### Events

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/events` | Paginated list with keyword/date/confidence filters | open |
| GET | `/api/v1/events/{id}` | Event detail + article timeline | open |
| GET | `/api/v1/events/{id}/timeline` | Day-grouped evolving timeline | open |
| POST | `/api/v1/events/merge` | Merge source event into target | admin |

### Chat & Reports

| Method | Path | Purpose | Auth |
|---|---|---|---|
| POST | `/api/v1/chat` | SSE streaming chat (fast + deep path) | user |
| GET | `/api/v1/conversations` | List chat conversations | user |
| POST | `/api/v1/reports/generate` | Generate executive report | analyst |
| GET | `/api/v1/reports` | List reports | analyst |
| GET | `/api/v1/reports/{id}` | Get report detail | analyst |

## 9. Authentication and authorization

- **Modes**: `AUTH_PROVIDER=none` (local bcrypt + HS256 JWT) or `clerk`/`auth0`
  (RS256 JWKS verification, identity sync).
- **Tokens**: access JWT 15 min; refresh token 30 days, hashed, rotates on use.
- **API keys**: `pls_`-prefixed, hashed at rest, scoped (`read`/`chat`/`reports`).
- **RBAC**: `user < analyst < admin`; enforced per route via `require_role`.
- **Security**: Redis rate limiting (fail-open), double-submit CSRF, SSRF guard,
  audit log for every auth action.

## 10. Key workflows

### Ingestion pipeline

```
scheduler_tick → list_due_sources → enqueue_poll → poll_source_job:
  fetch feed → dedupe (exact + fuzzy) → insert article → process_article_job:
    fetch page → extract content → store body → detect language → classify
    → enqueue_embed_article → enqueue_cluster_article
```

### Embedding pipeline

```
embed_article_job → chunk_text (256 tokens, 40 overlap) → BGE-M3 encode
  → upsert to Qdrant → enqueue_cluster_article
```

### Event clustering

1. **Fast path**: article vector vs open centroids → cosine ≥ 0.72 → attach
2. **Slow path** (every 30 min): UMAP+HDBSCAN on unmatched articles → new events
3. **Closure**: events idle 72h → closed, centroid deleted

### Chat

- **Fast path**: retrieve context → single LLM call → SSE stream with citations
- **Deep path**: planner → retriever×N → reasoner×N → synthesizer → SSE stream

### Notification rules

When a new event is created, active notification rules are checked. Matching rules
log a `notification_triggered` audit event. Delivery (email/in-app) is not yet
implemented.

## 11. Frontend

See [`frontend/README.md`](frontend/README.md) for full details.

Key points:
- Next.js 16 App Router, React 19, Tailwind CSS v4
- Kimi-inspired dark UI with warm orange accent
- 9 pages: login, register, search, events, chat, reports, admin, settings, home
- Protected routes via AuthGuard, 401 auto-redirect
- Command palette (Cmd+K), mobile hamburger menu, toast notifications
- SSE streaming chat with thinking indicators and evidence citations

## 12. Testing

```bash
uv run pytest              # unit + integration tests
uv run ruff check .        # lint
uv run ruff format --check .  # format
uv run lint-imports        # module boundaries
```

**Test isolation** (`tests/conftest.py`):
- Dedicated `pulseai_test` database, per-test truncation
- Fake embedders/rerankers (no model downloads in tests)
- `RATE_LIMIT_ENABLED=false`, `AUTH_PROVIDER=none`

## 13. Deployment

**Docker Compose** (`docker-compose.yml`): postgres, qdrant, redis, api, worker,
scheduler. App services use the same Dockerfile with different CMD overrides.

**CI** (GitHub Actions): ruff check → ruff format → lint-imports → migrations →
pytest with 80% coverage gate.

**Production**: strong `JWT_SECRET`, `COOKIE_SECURE=true`, S3-backed storage,
managed auth provider, real secrets via environment.

## 14. Troubleshooting

| Symptom | Fix |
|---|---|
| Port 8090 in use | Kill old process: `netstat -ano \| findstr :8090` then `taskkill /F /PID <pid>` |
| `/readyz` returns 503 | Start Docker: `docker compose up -d postgres qdrant redis` |
| Search returns 503 | Model loading (first search takes ~15s). Wait or check `~/.cache/huggingface` |
| No events created | Run `uv run pulseai-backfill-clusters` |
| Chat returns "service unavailable" | Start Ollama: `ollama serve` |
| Keyword search returns all events | Ensure `q` parameter is in the function signature (check with `grep "q:" backend/modules/events/router.py`) |
| CORS errors from frontend | Check CORS middleware is in `backend/main.py` |
| Frontend build fails | Run `rm -rf .next && npm run build` to clear cache |
