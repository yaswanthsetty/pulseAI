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
 ┌─────────────┐                    │  modules/auth        ✔ 1.5   │
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

## API surface

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/healthz`, `/readyz`, `/health` | liveness / readiness / legacy alias | open |
| POST | `/api/v1/auth/register` | local account creation (role `user`) | open |
| POST | `/api/v1/auth/login` | access token + rotating refresh cookie | open |
| POST | `/api/v1/auth/session` | exchange a Clerk/Auth0 JWT for PulseAI tokens | open |
| POST | `/api/v1/auth/refresh` | rotate refresh token (old one revoked) | cookie |
| POST | `/api/v1/auth/logout` | revoke refresh token, clear cookies | cookie |
| GET | `/api/v1/users/me` | current profile + role | user+ |
| GET | `/api/v1/users` | list users (admin) | admin |
| PATCH | `/api/v1/users/{id}/role` | change role (admin; audited) | admin |
| GET | `/api/v1/api-keys` | list API keys (raw never returned) | user+ |
| POST | `/api/v1/api-keys` | create API key (raw returned once) | user+ |
| DELETE | `/api/v1/api-keys/{id}` | revoke API key | user+ |
| GET | `/api/v1/sources` | list sources with health | user+ |
| POST | `/api/v1/sources` | add source (feed validated first, FR-4) | admin |
| PATCH | `/api/v1/sources/{id}` | update credibility / interval / status | admin |
| POST | `/api/v1/sources/{id}/poll` | queue an immediate poll | admin |
| GET | `/api/v1/articles` | list/filter articles (date, source, category, …) | open |
| GET | `/api/v1/articles/{id}` | article detail | open |
| POST | `/api/v1/search` | semantic search over Qdrant (BGE-small dense, chunk-level vectors, deduped by article) | open (rate-limited) |

Auth column = `require_role` minimum for the route per the §22 RBAC matrix
(user < analyst < admin; guests are unauthenticated). All errors use the spec
§19 envelope `{"error": {"code", "message", "request_id"}}`; all lists use
`{items, page, page_size, total}`.

## Authentication & RBAC (Phase 1.5)

- **Managed provider or local:** `AUTH_PROVIDER=none` enables local
  register/login (HS256 JWTs, bcrypt passwords). `AUTH_PROVIDER=clerk|auth0`
  verifies the provider's RS256 JWT against its JWKS and syncs the identity
  into the `users` table — provider JWTs work directly on any endpoint, and
  `POST /auth/session` exchanges one for PulseAI tokens.
- **Tokens:** access JWTs last 15 min (spec §20); refresh tokens last 30 days,
  are stored hashed, rotate on every use (old token revoked), and travel only
  as `HttpOnly; Secure; SameSite=Lax` cookies. Access tokens also work as
  cookies for browser/server-component flows.
- **API keys:** `pls_`-prefixed, scoped (`read`/`chat`/`reports`), hashed at
  rest, revocable, and returned to the client exactly once.
- **RBAC:** `require_role(min_role)` dependency enforces the §22 matrix per
  route (source management = admin, source listing = user+, browsing/search =
  guests) and is integration-tested for every endpoint × role.
- **Security controls (§23):** Redis sliding-window rate limiting (30/min
  anonymous per IP, 120/min authenticated; fail-open), double-submit CSRF
  tokens for cookie-authenticated mutations, and audit-log events for every
  auth action (`login`, `login_failed`, `refresh`, `logout`, `role_change`, …).
  The auth module is carved out as shared infrastructure in import-linter
  (business modules may import it; it never imports them).

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
- **Phase 1.5 (auth & RBAC) — complete:** managed provider (Clerk/Auth0)
  integration with user sync, local register/login fallback, 15-min access
  JWTs + rotating 30-day refresh cookies, hashed API keys, `require_role`
  enforcement of the §22 matrix, Redis sliding-window rate limiting, CSRF,
  and audit-logged auth events.
- **Phase 2 (embeddings) — core pipeline done:** sentence-aware token-bounded
  chunking (FR-8) → `article_chunks`; BGE-small dense embeddings (FR-9) upserted
  into the `pulseai_articles` Qdrant collection as UUID-keyed chunk points with
  article payloads (§11); async `embed` queue consumed by the worker (FR-10).
  `POST /api/v1/search` returns real, deduplicated results. New articles are
  embedded automatically after processing, and the scheduler's periodic
  reconcile (default every 60 min, spec §11) re-enqueues embed jobs for any
  article still missing embedded chunks — no manual steps needed.
  `uv run pulseai-backfill-embeddings --recreate` remains for one-shot
  rebuilds of the collection. Hybrid dense+sparse (BGE-M3) and reranking are
  Phase 4. The model and Qdrant client load lazily, so API startup never
  downloads a model.
- **Postgres driver:** sync `psycopg2` by default; set
  `POSTGRES_DRIVER=postgresql+asyncpg` for the async driver option (Phase 2
  async work). The sync engine and Alembic always strip the async prefix.
- **Next:** Phase 2 remaining (hybrid dense+sparse + rerank) → Phase 3 events.
  See `PROJECT_STATUS_AND_ROADMAP.md`.
