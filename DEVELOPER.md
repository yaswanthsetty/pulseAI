# PulseAI — Developer Documentation

**Living document.** Keep this file synchronized with the codebase: whenever you
add, change, or remove behavior, update the relevant section here. Verify against
the code — do not document assumptions. The authoritative sources are
`pyproject.toml`, `backend/core/config.py`, `.env.example`,
`backend/db/models.py`, the routers in `backend/modules/*/router.py`, and
`docker-compose.yml`.

Related documents: [`README.md`](README.md) (overview + quick start),
[`PROJECT_STATUS_AND_ROADMAP.md`](PROJECT_STATUS_AND_ROADMAP.md) (completion
status and roadmap).

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
11. [Testing](#11-testing)
12. [Deployment](#12-deployment)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Overview

PulseAI is a real-time AI news intelligence platform. It ingests global news
streams over RSS, de-duplicates and classifies articles, embeds them for semantic
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
- **Events** — incremental clustering: a fast centroid-match path on every new
  article, a scheduled UMAP + HDBSCAN slow path for new stories, automatic
  closure of stale events, and an events API with an evolving timeline view.

- **Chat & Reports (Phase 5)** — `POST /api/v1/chat` SSE streaming with fast-path
  (single retrieve→generate→cite) and deep-path (planner→retriever×N→reasoner×N→synthesizer)
  auto-routing; evidence agreement scoring; executive report generation; LLM usage tracking.

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
        │ retry markers (Redis)     │  modules/ranking  (reserved) │
 ┌─────────────┐                    │  modules/agents   (reserved) │
 │ RQ worker   │ ◄── 3 queues       │  modules/reports  (reserved) │
 └──────┬──────┘  ingest/embed/     └────────────┬────────────────┘
        │            cluster                     │
        ▼                                        ▼
 PostgreSQL ───────────► object storage ◄─── previews stay inline
 (metadata)             (full article bodies)
        └────► Qdrant (chunk vectors + event centroids) ◄──── search
```

**Processes** (same codebase, different entrypoints):

| Process | Entrypoint | Job |
|---|---|---|
| API | `pulseai-api` | FastAPI HTTP server (`:8000`), routers, error envelope |
| Scheduler | `pulseai-scheduler` | Every `scheduler_tick_seconds` (30s default): enqueue due source polls, backoff retries, periodic embedding reconcile + event slow-path/closure |
| Worker | `pulseai-worker` | RQ worker consuming the `ingest`, `embed`, and `cluster` queues |
| One-shot CLIs | `pulseai-backfill-embeddings` / `pulseai-backfill-clusters` | Manual whole-corpus backfills / reconciles |

**Module boundaries** (enforced by import-linter, `.importlinter`):

1. `modules/api` is the top layer — nothing else imports it.
2. Business modules (`retrieval`, `ranking`, `events`, `agents`, `reports`,
   `ingestion`) never import each other. Cross-module job hand-offs go through
   the `core.queue` hub, which registers each module's RQ jobs via deferred
   imports (ingestion → embed → cluster).
3. `modules/auth` is shared cross-cutting infrastructure — business modules may
   import it; it never imports them.

**Cross-cutting pieces in `core/`:**

- `config.py` — pydantic-settings; the single source of truth for every process.
- `database.py` — sync SQLAlchemy engine (`pool_pre_ping`), `SessionLocal`,
  `get_db` dependency, `session_scope` context manager, shared naming convention
  for Alembic.
- `queue.py` — Redis + RQ wiring: named queues, per-source poll locks, retry
  markers, stable job ids (`poll-`, `process-`, `embed-`, `cluster-{id}`),
  reconcile due-markers, and the slow-path `last_run` anchor.
- `storage.py` — object-storage abstraction (local filesystem default, S3
  optional) for full article bodies (`content_ref`).
- `logging.py` — structured JSON logging (plain text at DEBUG).
- `ssrf.py` — SSRF guard on every outbound fetch.
- `audit.py` — append-only `audit_log` helper (never raises).
- `pagination.py` — the `{items, page, page_size, total}` list envelope.

## 3. Tech stack

| Concern | Choice |
|---|---|
| Runtime | Python ≥ 3.14 (pinned in `.python-version` and `pyproject.toml`) |
| Web framework | FastAPI + uvicorn |
| ORM / migrations | SQLAlchemy 2.0 (sync `psycopg2` default; `asyncpg` optional) + Alembic |
| Data stores | PostgreSQL 15 (Docker), Redis 7 + RQ (job queues), Qdrant (vectors) |
| Embeddings | FlagEmbedding (BGE-M3, dense + sparse in one pass) |
| Reranker | sentence-transformers `CrossEncoder` (BGE-reranker-base) |
| Clustering | umap-learn + hdbscan (event slow path) |
| Parsing | feedparser (RSS/Atom), beautifulsoup4 (HTML → text), langdetect |
| Auth | bcrypt, PyJWT (HS256 local; RS256 via JWKS for Clerk/Auth0) |
| HTTP | httpx |
| Dev / CI | pytest + pytest-cov, ruff, import-linter |

Runtime dependencies are declared in `pyproject.toml` (uv-managed, `uv.lock`);
`requirements.txt` is a plain-pip mirror for non-uv environments. `boto3` is an
optional extra (`pulseai[storage-s3]`).

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
  main.py                 # FastAPI app: lifespan (seed), routers, error envelope
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
    models.py             # all 17 tables (SQLAlchemy 2 mapped_column style)
    seed.py / seed_data.py  # idempotent reference-data seeding
  modules/
    api/                  # health.py + router.py (aggregate /api/v1, global rate-limit dep)
    auth/                 # router, service, security, deps (RBAC), ratelimit, csrf, schemas
    ingestion/            # service, jobs, parser, fetcher, classifier, dedupe, router, schemas, seeds
    retrieval/            # service (embed + search + rerank), chunker, jobs, router, schemas
    events/               # service (fast path, slow path, closure), jobs, router, schemas
    ranking/              # Phase 4 intent-aware scoring (blend_scores, detect_intent)
    agents/               # Phase 5: schemas, service (fast/deep path, agreement), router
    reports/              # Phase 5 placeholder (reports served via agents router)
  workers/
    worker.py             # RQ worker (SimpleWorker on Windows — no fork)
    scheduler.py          # poll scheduling + periodic reconciles
    backfill.py           # one-shot CLIs + periodic reconcile twins
migrations/               # Alembic env + versions/ (5 revisions)
tests/
  conftest.py             # dedicated pulseai_test DB, per-test truncation, fixtures
  unit/                   # chunker, dedupe, parser, classifier, retrieval, embedding
                          #   pipeline, events, backfill, security, ssrf, storage, consistency
  integration/            # auth, managed provider, rbac, rate limit, health, sources,
                          #   articles/ingestion, search, events
  fixtures/               # article.html, feed.xml
Dockerfile / docker-compose.yml / .github/workflows/ci.yml
pyproject.toml / uv.lock / requirements.txt / .importlinter / alembic.ini
.env.example / .python-version
README.md / DEVELOPER.md / PROJECT_STATUS_AND_ROADMAP.md
```

## 5. Setup and local development

Prerequisites: **Docker** (Postgres/Qdrant/Redis) and **uv** (Python ≥ 3.14).

```bash
# 1. Infrastructure (Postgres on :5434, Qdrant on :6333, Redis on :6379)
docker compose up -d postgres qdrant redis

# 2. Environment (required: POSTGRES_* have no defaults)
cp .env.example .env

# 3. Dependencies + schema
uv sync
uv run alembic upgrade head

# 4. Run the three processes (three terminals)
uv run pulseai-api          # http://localhost:8000 — Swagger at /docs
uv run pulseai-scheduler    # per-source polling + periodic reconciles
uv run pulseai-worker       # RQ job executor (ingest/embed/cluster queues)
```

The API seeds reference data (categories, countries, languages, ranking weights)
on startup; with `SEED_DEFAULT_SOURCES=true` it also adds three demo RSS sources.
The end-to-end flow: scheduler polls a source → worker fetches the feed, dedupes,
stores new articles, fetches + classifies bodies → enqueues `embed` → enqueues
`cluster`.

**Model downloads (lazy, per process):** the first search downloads BGE-M3
(~2.3 GB); the first reranked search downloads BGE-reranker-base (~1.1 GB). They
cache under `~/.cache/huggingface`. API startup never downloads a model.

**Seed a fresh corpus** (if the demo feeds return nothing, add any working RSS
feed via the admin API or a direct DB insert):

```bash
uv run pulseai-backfill-embeddings   # chunk + embed every unembedded article
uv run pulseai-backfill-clusters     # cluster unmatched articles into events
```

## 6. Configuration

All settings live in `backend/core/config.py` (pydantic-settings; read from the
environment, then `.env`). `.env.example` is the documented template. Summary of
the groups:

| Group | Variables | Defaults / notes |
|---|---|---|
| App | `ENVIRONMENT`, `DEBUG`, `APP_NAME` | `development` / `false` / `PulseAI` |
| Postgres | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DRIVER` | **required** (no defaults); host `localhost`, port `5434` (Docker maps 5434→5432); driver `postgresql` (sync) or `postgresql+asyncpg` |
| Qdrant | `QDRANT_URL` | `http://localhost:6333` |
| Redis | `REDIS_URL` | `redis://localhost:6379/0` |
| Storage | `STORAGE_BACKEND` (`local`/`s3`), `STORAGE_LOCAL_DIR`, `STORAGE_S3_BUCKET`, `STORAGE_S3_REGION` | local default; S3 needs the `[storage-s3]` extra |
| Auth | `AUTH_PROVIDER` (`none`/`clerk`/`auth0`), `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`, `JWT_ACCESS_TTL_MINUTES` (15), `REFRESH_TTL_DAYS` (30), `CLERK_DOMAIN`, `AUTH0_DOMAIN`, `AUTH0_AUDIENCE`, `COOKIE_SECURE`, `CSRF_ENABLED` | local mode default; **set a strong `JWT_SECRET` outside dev** |
| Rate limit | `RATE_LIMIT_ENABLED`, `RATE_LIMIT_ANON_PER_MINUTE` (30), `RATE_LIMIT_AUTH_PER_MINUTE` (120), `RATE_LIMIT_CHAT_PER_MINUTE` (10) | Redis sliding window, fail-open |
| Embeddings | `EMBEDDING_MODEL` (BAAI/bge-m3), `EMBEDDING_SIZE` (1024), `EMBEDDING_BATCH_SIZE` (16), `CHUNK_TARGET_TOKENS` (256), `CHUNK_OVERLAP_TOKENS` (40), `SINGLE_CHUNK_MAX_TOKENS` (300), `QDRANT_SHARDS` (2), `EMBEDDING_RECONCILE_INTERVAL_MINUTES` (60), `RECONCILE_DRIFT_ALERT_THRESHOLD` (10) | chunking parameters; reconcile cadence |
| Rerank | `RERANKER_MODEL` (BAAI/bge-reranker-base), `RERANK_ENABLED` (true), `RERANK_TOP_K` (50), `RERANK_TOP_N` (10) | top-K candidates → cross-encoder → top-N |
| Events | `QDRANT_ARTICLES_COLLECTION` (pulseai_articles), `QDRANT_EVENT_CENTROIDS_COLLECTION` (pulseai_event_centroids), `EVENT_MATCH_THRESHOLD` (0.72 — tuned against the live corpus; the spec's suggested 0.82 measures ~40% recall on BGE-M3 centroids), `EVENT_SLOW_PATH_WINDOW_HOURS` (6), `EVENT_SLOW_PATH_INTERVAL_MINUTES` (30), `EVENT_MIN_CLUSTER_SIZE` (3), `EVENT_CLOSE_HOURS` (72), `EVENT_UMAP_COMPONENTS` (5) | fast path / slow path / closure |
| LLM Summary | `SUMMARY_PROVIDER` (ollama), `SUMMARY_MODEL` (qwen3.5:9b), `OLLAMA_URL` (http://localhost:11434), `SUMMARY_MAX_TOKENS` (300), `SUMMARY_TIMEOUT_SECONDS` (120) | abstractive event summaries via Ollama; set `SUMMARY_PROVIDER=none` to disable |
| **Chat (Phase 5)** | `CHAT_PROVIDER` (ollama), `CHAT_MODEL` (qwen3.5:9b), `CHAT_MAX_TOKENS` (500), `CHAT_TIMEOUT_SECONDS` (120) | fast/deep-path chat; same Ollama endpoint as summaries |
| Ingestion | `SEED_DEFAULT_SOURCES` (true), `SCHEDULER_TICK_SECONDS` (30), `MIN_POLL_INTERVAL_MINUTES` (5), `DEFAULT_POLL_INTERVAL_MINUTES` (15), `FEED_FETCH_TIMEOUT_SECONDS` (15), `ARTICLE_FETCH_TIMEOUT_SECONDS` (10), `RETRY_BACKOFF_MINUTES` ([1,5,30]), `FUZZY_DUPLICATE_THRESHOLD` (0.92), `FUZZY_DUPLICATE_WINDOW_HOURS` (6), `SUPPORTED_LANGUAGES` ([en]), `HTTP_USER_AGENT`, `MAX_ARTICLE_STORAGE_CHARS` (50000), `CONTENT_PREVIEW_CHARS` (500) | polling, dedupe, fetching knobs |

## 7. Database

22 tables in `backend/db/models.py`; migrations live in `migrations/versions/`
(5 revisions). Groups:

| Group | Tables |
|---|---|
| Lookup (seeded) | `categories`, `countries`, `languages`, `ranking_configs` |
| Ingestion | `sources`, `articles`, `article_chunks` |
| Events | `events`, `event_articles` |
| Identity | `users`, `api_keys`, `refresh_tokens` |
| User content | `saved_reports`, `saved_searches`, `bookmarks`, `notification_rules` |
| Ops | `audit_log` |
| **Chat (Phase 5)** | `conversations`, `conversation_messages` (with `evidence_agreement` float) |
| **Reports (Phase 5)** | `reports` |
| **Usage tracking (Phase 5)** | `llm_usage` |

Design points:

- **Article bodies are out-of-line**: Postgres stores metadata + a ~500-char
  `content_preview`; the full text lives in object storage behind `content_ref`.
- **Dedupe**: `articles.url_hash` (SHA-256 of the normalized URL) is a unique
  index (fast path); fuzzy title similarity is checked in code (slow path).
- **Chunks**: `article_chunks` rows mirror Qdrant points 1:1 (`qdrant_point_id`
  = chunk UUID = the Qdrant point id); `embedding_status` is
  `pending | embedded | failed`.
- **Events**: `events.article_count` is a maintained counter; `event_articles`
  rows are the source of truth for membership (timeline endpoints derive totals
  from the rows, not the counter). Closed events keep their Postgres history.
- **Classification is FK-safe**: `category_code`, `country_code`,
  `language_code` reference the seeded lookup tables.
- Constraint/index names are generated from a shared naming convention in
  `core/database.py` so Alembic produces stable names.

Schema changes: edit `models.py`, then
`uv run alembic revision --autogenerate -m "..."`, review the generated file,
`uv run alembic upgrade head`. CI verifies migrations apply on a fresh database.

## 8. API

All responses use the error envelope
`{"error": {"code", "message", "request_id"}}`; all lists use
`{items, page, page_size, total}`. Interactive docs: `/docs`.

| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/healthz` | liveness | open |
| GET | `/readyz` | readiness (Postgres, Qdrant, Redis) — 503 when not ready | open |
| GET | `/health` | legacy alias | open |
| POST | `/api/v1/auth/register` | local account (role `user`) | open |
| POST | `/api/v1/auth/login` | access token + rotating refresh cookie | open |
| POST | `/api/v1/auth/session` | exchange Clerk/Auth0 JWT for PulseAI tokens | open |
| POST | `/api/v1/auth/refresh` | rotate refresh token (old revoked) | cookie |
| POST | `/api/v1/auth/logout` | revoke refresh, clear cookies | cookie |
| GET | `/api/v1/users/me` | current profile + role | user+ |
| GET | `/api/v1/users` | list users | admin |
| PATCH | `/api/v1/users/{id}/role` | change role (audited) | admin |
| GET/POST | `/api/v1/api-keys` | list / create (raw returned once) | user+ |
| DELETE | `/api/v1/api-keys/{id}` | revoke | user+ |
| GET | `/api/v1/sources` | list sources with health | user+ |
| POST | `/api/v1/sources` | add source (feed validated first) | admin |
| PATCH | `/api/v1/sources/{id}` | update credibility/interval/status | admin |
| POST | `/api/v1/sources/{id}/poll` | queue an immediate poll | admin |
| GET | `/api/v1/articles` | list/filter articles (date, source, category, country, language, event) | open |
| GET | `/api/v1/articles/{id}` | article detail | open |
| POST | `/api/v1/search` | semantic/keyword/hybrid search; body `{query, top_k, mode, filters}` | open (rate-limited) |
| GET | `/api/v1/events` | paginated events; filters `date_from/date_to/category_code/min_confidence` | open |
| GET | `/api/v1/events/{id}` | event detail + article timeline | open |
| GET | `/api/v1/events/{id}/timeline` | articles grouped by day; per-day headline + keywords + titles | open |

**External integrations:**

- **Clerk / Auth0** (optional): provider JWTs verified via JWKS (RS256, TTL-cached
  1h), identity upserted into `users`; roles read from provider metadata.
- **S3-compatible storage** (optional): Supabase Storage / MinIO / AWS via boto3.
- **Hugging Face Hub**: model downloads at first use (BGE-M3, BGE-reranker-base).

## 9. Authentication and authorization

`backend/modules/auth/` implements the full auth surface.

- **Modes** (`AUTH_PROVIDER`): `none` = local register/login (bcrypt passwords,
  HS256 access JWTs); `clerk`/`auth0` = provider RS256 JWTs verified against the
  cached JWKS and synced into `users` (provider tokens work directly on any
  endpoint; `POST /auth/session` exchanges one for PulseAI tokens).
- **Tokens**: access JWT 15 min (`jwt_access_ttl_minutes`); refresh token 30 days,
  stored **hashed** (SHA-256), rotates on every use (old token revoked), travels
  only as an `HttpOnly; Secure; SameSite=Lax` cookie. Access tokens also work as
  cookies for browser flows.
- **API keys**: `pls_`-prefixed, hashed at rest, returned raw exactly once,
  scopes `read | chat | reports` (enforced for key principals).
- **RBAC**: `user < analyst < admin`; the `require_role(min_role)` dependency
  enforces it per route and is integration-tested for every endpoint × role
  (`tests/integration/test_rbac.py`). Guests are simply unauthenticated.
- **Principal resolution** (`deps.get_current_user`): Bearer access JWT → Bearer
  provider JWT → Bearer `pls_` API key → `pulseai_access` cookie.
- **Security controls**: Redis sliding-window rate limiting (30/min anonymous per
  IP, 120/min authenticated; **fail-open**), double-submit CSRF for
  cookie-authenticated mutations (`X-CSRF-Token` must echo the `pulseai_csrf`
  cookie; bearer-authenticated requests are skipped), SSRF guard on all outbound
  fetches, and audit-log events for every auth action.

## 10. Key workflows

### Ingestion pipeline

```
scheduler_tick ──► list_due_sources (active, healthy, interval elapsed)
   └─► enqueue_poll (stable job id, per-source Redis lock)
        └─► poll_source_job ──► poll_source:
             fetch feed (SSRF-guarded, timeout) → clear failures → parse_feed
             → for each entry: normalize_url + url_hash (exact dup? skip)
               → find_fuzzy_duplicate (same source, ±6h, title ≥0.92? skip)
               → insert article → enqueue_process_article (after commit)
        └─► process_article_job ──► process_article:
             fetch page (SSRF-guarded) → extract_main_content (BeautifulSoup)
             → store body to object storage (content_ref) + preview
             → detect_language (langdetect, if unset) → classify_category
               (deterministic keyword scorer over the fixed taxonomy)
             → mark processed_at → enqueue_embed_article
```

- **Backoff retries**: on fetch failure, `consecutive_failures` increments;
  retries are scheduled at `RETRY_BACKOFF_MINUTES` (1/5/30) as Redis TTL markers
  consumed by the scheduler; after the schedule is exhausted the source is marked
  `degraded` (audited). A successful fetch resets failures and restores `active`.
- **Windows note**: RQ's default worker forks (POSIX-only); `workers/worker.py`
  falls back to `SimpleWorker` (in-process) on Windows, and delayed jobs are
  driven by the scheduler's Redis TTL markers instead of RQ's scheduler.

### Embedding pipeline

```
embed_article_job ──► embed_article:
   already fully embedded? → no-op (idempotent)
   no chunks? → chunk_text (256-token target, 40-token sentence-aligned
                overlap, <300 tokens = single chunk; token estimate = 1.3/word)
   pending chunks → BGE-M3 encode (dense + sparse in one pass, batch 16)
     → upsert points into pulseai_articles (named dense+sparse vectors,
       2 shards; point id = chunk UUID; full searchable payload)
   on failure → chunks marked `failed`, exception raised → RQ retry (3×)
   on success → enqueue_cluster_article (event handoff)
```

- Lazy model/client loading: importing the API never downloads a model.
- The periodic **reconcile** (`reconcile_embeddings`, scheduler, default every
  60 min) re-enqueues embed jobs for articles missing embedded chunks and syncs
  Postgres ↔ Qdrant in both directions (orphan point purge, missing chunk
  re-mark), alerting on drift beyond `RECONCILE_DRIFT_ALERT_THRESHOLD`.

### Search and reranking

`search()` in `retrieval/service.py`: encode the query with BGE-M3 → retrieve
from Qdrant in the requested `mode` (`semantic` = dense, `keyword` = sparse,
`hybrid` = RRF fusion) with payload filters → dedupe by article (keep each
article's best chunk score) → **rerank**: the top-K candidates
(`rerank_top_k` = 50) are re-scored by the cross-encoder and the top-N
(`rerank_top_n` = 10) returned.

The reranker loads lazily; **any failure degrades to retrieval order** — a
rerank problem never makes search fail (covered by integration tests).

### Event clustering

Two paths + closure, all in `events/service.py`:

1. **Fast path** — after each successful embed, `cluster_article_job` computes
   the article vector (mean of its chunk dense vectors — no extra model call) and
   queries the `pulseai_event_centroids` collection (one point per *open* event).
   Cosine ≥ `EVENT_MATCH_THRESHOLD` (0.72) → attach to that event, grow the
   centroid as a **running average**, bump `article_count` + `last_updated`. The
   centroid upsert happens before the Postgres commit; a Qdrant failure rolls the
   whole transaction back (no half-created events).
2. **Slow path** — `reconcile_events` (scheduler, every 30 min, Redis-marker
   gated) runs **UMAP + HDBSCAN** over unmatched embedded articles and creates an
   event per cluster (title from the most-central member, extractive summary
   as fallback, **abstractive LLM summary** generated via Ollama on success,
   confidence = mean member similarity to the centroid, plus a centroid point). The window is
   bounded (`EVENT_SLOW_PATH_WINDOW_HOURS` = 6) but **self-healing**: after any
   downtime or failed pass it widens to cover the gap since the last successful
   run (`cluster:slow_path:last_run`), so older unmatched articles are never
   stranded.
3. **Closure** — events with no new article for `EVENT_CLOSE_HOURS` (72h) are
   marked `closed` and dropped from the centroid collection (Postgres history is
   kept). `close_stale_events` tolerates a failed Qdrant delete (logged).

**Timeline endpoint** (`GET /events/{id}/timeline`): groups the event's articles
by day, with a per-day headline (the title closest to the event centroid — highest
`similarity_at_match` — else the first published), distinctive keywords
(stopword-filtered title terms), and the day's titles in publication order.
`total_articles` is derived from the returned rows, not the counter.

### One-shot CLIs

- `pulseai-backfill-embeddings [--recreate]` — enqueue embed jobs for every
  article missing fully-embedded chunks; `--recreate` deletes the Qdrant
  collection and resets `article_chunks` for a full re-chunk + re-embed.
- `pulseai-backfill-clusters` — the slow path over the **whole** corpus (no time
  window) + closure; resets the slow-path `last_run` anchor.

## 11. Testing

Run with the Docker infra up (Postgres on :5434, Redis, Qdrant):

```bash
uv run pytest              # 244 tests (unit + integration)
uv run pytest tests/unit/test_events.py -q
```

**How the suite is isolated** (`tests/conftest.py`):

- Environment is forced **before** any backend import: `POSTGRES_DB=pulseai_test`
  (never the dev DB), a temp `STORAGE_LOCAL_DIR`, `AUTH_PROVIDER=none`,
  `RATE_LIMIT_ENABLED=false`, `CSRF_ENABLED=true`, `RERANK_ENABLED=false`.
- A session-scoped fixture creates/migrates `pulseai_test` and seeds reference
  data; an autouse fixture **truncates all mutable tables before every test**
  (lookup tables are preserved).
- Fixtures: `db` (clean session), `client` (FastAPI TestClient), `make_user`
  (register + login any role), `csrf_headers`.
- Model downloads are avoided: unit tests inject fake embedders/rerankers and a
  fake Qdrant client; integration tests exercise the degraded (no-rerank) path.

**Test files** — unit: chunker, dedupe, parser, classifier, retrieval (incl.
rerank), embedding pipeline, events, events-consistency (rollback + counter
drift), backfill (incl. self-healing window), security, ssrf, storage.
Integration: auth API, managed provider, RBAC matrix, rate limit, health,
sources API, ingestion (articles), search API, events API (incl. timeline).

**Quality gates** (local and CI):

```bash
uv run ruff check .          # lint (line length 100)
uv run ruff format .         # format (canonical py314 style)
uv run lint-imports          # 4 module-boundary contracts (.importlinter)
uv run pytest --cov=backend --cov-fail-under=80   # coverage gate (80%)
```

## 12. Deployment

**Docker Compose** (`docker-compose.yml`, project name `pulseai`): `postgres`
(15-alpine, host port 5434), `qdrant` (host 6333/6334), `redis` (7-alpine, 6379),
and the three app services `api` / `worker` / `scheduler` built from the same
`Dockerfile` (compose overrides `CMD`; `uv run --no-sync pulseai-*`). A shared
volume `storage_data` keeps article bodies consistent across processes. Infra
services have healthchecks; app services depend on them.

```bash
docker compose up --build -d
docker compose exec api uv run alembic upgrade head
```

**Dockerfile**: `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`; `uv sync
--frozen --no-dev` (deps cached unless `pyproject.toml`/`uv.lock` change);
copies `backend/`, `migrations/`, `alembic.ini`; `PYTHONPATH=/app`.

**CI** (`.github/workflows/ci.yml`, every push to `main`/`develop` + PRs):
concurrency-cancelled, 30-min timeout, service containers (postgres 15 on 5434,
redis 7, **qdrant pinned `v1.18.2`** — dense + sparse named vectors need ≥1.13).
Steps: `uv sync --frozen` → `ruff check` → `ruff format --check` →
`lint-imports` → `alembic upgrade head` on a fresh DB → `pytest --cov
--cov-fail-under=80`. No test touches the real embedding model (all fakes), so
CI never downloads the 2.3 GB model.

**Production notes** (see also `.env.example` comments): strong `JWT_SECRET`,
`COOKIE_SECURE=true` behind HTTPS, a managed `AUTH_PROVIDER` or rotated JWT
secrets, S3-backed storage, real secrets via environment (never commit `.env`),
and `ENVIRONMENT=production`.


## 12. Frontend (Phase 6)

The frontend is a Next.js 16 App Router application in `frontend/`. It provides
a Kimi-inspired dark UI for searching, browsing events, chatting with the RAG
pipeline, and generating executive reports.

```bash
cd frontend
npm install
npm run dev    # http://localhost:3000
```

**Tech stack:** Next.js 16 (Turbopack), TypeScript, Tailwind CSS v4 (`@theme inline`),
TanStack Query (data fetching + caching).

**Design system:**

| Token | Hex | Role |
|---|---|---|
| `background` | `#0f1117` | Dark base |
| `card` | `#181a20` | Card/panel surfaces |
| `primary` | `#ff6b35` | Accent / CTA (orange) |
| `success` | `#22c55e` | Resolved states |
| `muted` | `#6b7280` | Secondary text |

Fonts: Space Grotesk (display), Inter (body), JetBrains Mono (data).

**Pages:**

| Route | Description |
|---|---|
| `/login` | Email/password auth with validation, password toggle, auto-redirect |
| `/register` | Account creation with name/email/password, auto-login after register |
| `/search` | Semantic / keyword / hybrid mode selector, result cards with scores |
| `/events` | Event cards with confidence indicators, skeleton loading |
| `/chat` | SSE streaming chat, thinking states, evidence citations with `[N]` IDs |
| `/reports` | Topic input + timeframe selector, report cards with status badges |

**Architecture:**

- `Shell.tsx` — sidebar navigation (logo, "New Chat" button, nav items, sign out)
  with mobile hamburger menu and overlay
- `AuthGuard.tsx` — redirects to `/login` if no token in localStorage
- `Toast.tsx` — global notification system (success / error / info, auto-dismiss)
- `api.ts` — API client with auth headers, SSE streaming wrapper, 401 handling

**Auth flow:** Token stored in `localStorage` (`pulseai_token`). All API requests
include `Authorization: Bearer <token>`. Login/register pages redirect to `/search`
if already authenticated. Sidebar has sign-out button that clears token.

**Environment:** `NEXT_PUBLIC_API_URL=http://127.0.0.1:8090` in `frontend/.env.local`.

**React best practices applied:**

- `useCallback` on all event handlers passed as props
- `React.memo` on `MessageBubble` to prevent re-rendering all messages per token
- `useRef` for input values in streaming callbacks (avoids stale closures)
- Hoisted `Intl.DateTimeFormat` instances (avoids per-render allocation)
- Ternary conditionals (`? :`) instead of `&&` for conditional rendering
- Hoisted static data (suggestion queries, color maps) outside components

## 13. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `uv run pulseai-api` exits immediately / port in use | The API binds `:8000` by default (`backend.main.run`). Check `netstat -ano \| grep ':8000'`; stop the old process or run `uv run uvicorn backend.main:app --port 8090`. (Port 8000 is often reserved by Hyper-V on Windows.) |
| `/readyz` returns 503 | One of Postgres/Qdrant/Redis is unreachable — the body lists which check failed. Start the containers: `docker compose up -d postgres qdrant redis`. |
| Search returns 503 "temporarily unavailable" | Model download failure or Qdrant down. The first search downloads BGE-M3 (~2.3 GB) — allow time/bandwidth; check `~/.cache/huggingface`. |
| Search works but results are in retrieval order (no rerank) | The reranker failed to load — that is the designed graceful degradation. Check the API log for "reranker unavailable". |
| `pytest` fails at collection/setup | Docker infra not running (the test DB `pulseai_test` is created on demand, but Postgres must be reachable on :5434) or a stale cache. `docker compose up -d postgres qdrant redis` then re-run. |
| No events are created | Run `uv run pulseai-backfill-clusters` once, or confirm the scheduler process is running (it drives `reconcile_events`). Unmatched articles need embedded chunks first (`pulseai-backfill-embeddings`). |
| Event summaries are extractive (not LLM-generated) | Ollama is not running or the model is unavailable. Start Ollama (`ollama serve`), verify with `curl http://localhost:11434/api/tags`, then re-cluster: `uv run pulseai-backfill-clusters`. Set `SUMMARY_PROVIDER=none` to disable. |
| Ollama summary returns empty content | Qwen 3.5 is a thinking model — it uses all tokens for reasoning. The code passes `think: false` to disable this. If using a different model, ensure it supports the `think` parameter or remove it. |
| `FlagReranker` / `prepare_for_model` errors | The code intentionally uses sentence-transformers `CrossEncoder` (transformers 5.x removed `prepare_for_model`). Don't switch back to `FlagReranker` without verifying against the pinned transformers. |
| Migration autogenerate picks up unrelated changes | The shared naming convention in `core/database.py` keeps names stable; review every autogenerated revision before applying. |
| Worker does nothing on Windows | Worker uses `SimpleWorker` (no fork). Jobs are still picked up; delayed/backoff jobs are driven by the scheduler process, which must be running. |
| `ruff format` rewrites my parentheses around `except` | The project targets Python 3.14, where ruff's canonical style is the unparenthesized multi-exception `except` — that's expected, not a bug. |
| Corpus changed but search results look stale | Re-run `uv run pulseai-backfill-embeddings --recreate` to rebuild collection + chunks, then `uv run pulseai-backfill-clusters` to rebuild events. |
| `.env` missing | `POSTGRES_*` are required (no defaults) — `cp .env.example .env` before starting anything. |
