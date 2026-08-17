# PulseAI

Real-time AI news intelligence platform — a modular-monolith backend that ingests
global news streams, de-duplicates and classifies articles, embeds them for
semantic search, and groups duplicate coverage into evolving events.

> **New here?** Read [`DEVELOPER.md`](DEVELOPER.md) — the living developer
> documentation (architecture, setup, environment, schema, workflows, testing,
> deployment, troubleshooting). Keep it updated when you change the code.

## Architecture

```
 News sources (RSS)                 ┌─────────────────────────────┐
        │                           │  FastAPI application         │
        ▼                           │  modules/api (routers)       │
 ┌─────────────┐   enqueue          │  modules/ingestion           │
 │  scheduler  ├──────────────────► │  modules/retrieval           │
 └─────────────┘    poll jobs       │  modules/events              │
        ▲                           │  modules/auth                │
        │ retry markers (Redis)     │  modules/ranking  (reserved) │
 ┌─────────────┐                    │  modules/agents   (reserved) │
 │ RQ worker   │ ◄── ingest queue   │  modules/reports  (reserved) │
 └──────┬──────┘                    └────────────┬────────────────┘
        │                                        │
        ▼                                        ▼
 PostgreSQL ───────────► object storage ◄─── previews stay inline
 (metadata)             (full article bodies)
```

- **Modular monolith**: strictly-bounded modules; `modules/api` is the only top
  layer; sibling imports are rejected by **import-linter** in CI.
- **Decoupled processes:** `api` (HTTP), `scheduler` (per-source polling), and
  `worker` (RQ, executes poll/process/embed/cluster jobs). Backoff retries are
  driven by Redis TTL markers, so the same code runs on Linux and Windows.
- **Object storage:** full article bodies live out-of-line (`content_ref`);
  Postgres keeps metadata + a ~500-char preview.

## Tech stack

Python 3.14 · FastAPI · SQLAlchemy 2 + Alembic · PostgreSQL 15 · Redis 7 + RQ ·
Qdrant (vector store) · BGE-M3 embeddings · BGE-reranker cross-encoder · UMAP +
HDBSCAN event clustering · BeautifulSoup + feedparser · langdetect · ruff +
pytest + import-linter.

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
uv run pulseai-scheduler            # per-source polling
uv run pulseai-worker               # RQ job executor
```

The API seeds three demo RSS sources on startup (`SEED_DEFAULT_SOURCES=true`);
the scheduler polls them and the worker processes articles end to end
(dedupe → clean → classify → store → embed → cluster).

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
| POST | `/api/v1/sources` | add source (feed validated first) | admin |
| PATCH | `/api/v1/sources/{id}` | update credibility / interval / status | admin |
| POST | `/api/v1/sources/{id}/poll` | queue an immediate poll | admin |
| GET | `/api/v1/articles` | list/filter articles (date, source, category, …) | open |
| GET | `/api/v1/articles/{id}` | article detail | open |
| POST | `/api/v1/search` | semantic/keyword/hybrid search (BGE-M3 dense+sparse, reranked, deduped by article) | open (rate-limited) |
| GET | `/api/v1/events` | paginated events with date/category/confidence filters | open |
| GET | `/api/v1/events/{id}` | event detail + article timeline | open |
| GET | `/api/v1/events/{id}/timeline` | articles grouped by day — per-day headline + keywords | open |

Auth column = `require_role` minimum for the route (`user < analyst < admin`;
guests are unauthenticated). All errors use the envelope
`{"error": {"code", "message", "request_id"}}`; all lists use
`{items, page, page_size, total}`.

## Authentication & RBAC

- **Managed provider or local:** `AUTH_PROVIDER=none` enables local
  register/login (HS256 JWTs, bcrypt passwords). `AUTH_PROVIDER=clerk|auth0`
  verifies the provider's RS256 JWT against its JWKS and syncs the identity
  into the `users` table — provider JWTs work directly on any endpoint, and
  `POST /auth/session` exchanges one for PulseAI tokens.
- **Tokens:** access JWTs last 15 min; refresh tokens last 30 days, are stored
  hashed, rotate on every use (old token revoked), and travel only as
  `HttpOnly; Secure; SameSite=Lax` cookies.
- **API keys:** `pls_`-prefixed, scoped (`read`/`chat`/`reports`), hashed at
  rest, revocable, and returned to the client exactly once.
- **RBAC:** `require_role(min_role)` enforces the role matrix per route (source
  management = admin, source listing = user+, browsing/search = guests) and is
  integration-tested for every endpoint × role.
- **Security controls:** Redis sliding-window rate limiting (30/min anonymous
  per IP, 120/min authenticated; fail-open), double-submit CSRF for
  cookie-authenticated mutations, SSRF protection on all outbound fetches, and
  audit-log events for every auth action.

## Quality gates

```bash
uv run ruff check .        # lint
uv run ruff format .       # format
uv run lint-imports        # module boundaries
uv run pytest              # unit + integration tests (need docker infra up)
```

CI (GitHub Actions) runs all four gates plus `alembic upgrade head` on a fresh
database and a pytest coverage floor of 80%.

## Project status

See [`PROJECT_STATUS_AND_ROADMAP.md`](PROJECT_STATUS_AND_ROADMAP.md) for the
completion status, the spec-by-spec checklist, and what is planned next.
