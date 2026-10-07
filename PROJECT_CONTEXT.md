# PulseAI — Complete Project & Architecture Context

> **Target Audience:** AI models, coding agents, and software engineers working on PulseAI.
> This document provides an exhaustive, code-verified explanation of the architecture, data flow, features, database schemas, APIs, configuration, background workers, and conventions so that any AI model can immediately understand and modify the codebase without prior re-analysis.

---

## 1. Project Overview & Purpose

**PulseAI** is a production-grade, real-time AI news intelligence platform designed as a **modular monolith**. It automatically monitors global news streams, cleans and deduplicates stories, generates hybrid embeddings, clusters related stories into evolving real-time "events", ranks search results by temporal intent and source credibility, and exposes multi-tiered AI reasoning (RAG chat, executive reports, and abstractive summaries) powered by local LLMs (via Ollama).

### Core Capabilities
1. **Automated RSS Ingestion:** Scheduled polling of RSS/Atom feeds with polite per-source crawling intervals, exponential backoff on failures, and SSRF-safe fetching.
2. **Deduplication & Extraction:** Exact URL hashing (`SHA-256`) and fuzzy title matching (Levenshtein similarity >= 0.92 in a 6-hour window) to eliminate duplicate coverage. Article bodies are extracted from HTML and stored out-of-line in object storage (local disk or S3), keeping PostgreSQL rows lightweight with preview text (< 500 characters).
3. **Classification & NLP:** Automatic language detection (`langdetect`) and category classification across 9 taxonomy categories (`politics`, `business`, `technology`, `science`, `health`, `sports`, `entertainment`, `world`, `other`).
4. **Hybrid Embeddings (BGE-M3):** Sentence-aware, token-bounded chunking (256-token target, 40-token overlap, <300 token single-chunk bypass). Generates dense (1024-dim) and sparse lexical weights in a single pass into a sharded Qdrant collection.
5. **Multi-Mode Search & Cross-Encoder Reranking:** Supports `semantic` (dense), `keyword` (sparse), and `hybrid` (Reciprocal Rank Fusion - RRF) search with metadata filtering. Top-50 candidates are reranked using a cross-encoder (`BAAI/bge-reranker-base`).
6. **Intent-Aware Temporal Ranking:** Blends similarity, freshness (exponential half-life decay), source credibility score, and event membership based on detected query intent (`recency`, `historical`, `default`) using database-driven weights.
7. **Incremental Event Clustering:**
   - **Fast Path:** New article embeddings are matched against open event centroids in Qdrant (cosine similarity >= 0.72). Matching updates the centroid as a running average.
   - **Slow Path:** Scheduled UMAP (5 components) + HDBSCAN clustering over unmatched articles creates new events with extractive summaries and LLM abstractive summaries.
   - **Event Lifecycle:** Inactive events (>72h with no new articles) are automatically marked `closed` and their centroids are purged from Qdrant.
8. **Interactive AI Agents & Reporting:**
   - **Fast-Path RAG Chat:** Server-Sent Events (SSE) streaming answers with cited article evidence `[#1]`, `[#2]` and evidence agreement scores.
   - **Deep-Path Multi-Step Reasoning:** Planner -> Multi-Retriever -> Reasoner -> Synthesizer pipeline with live `thinking` progress events for complex analytical questions.
   - **Executive Reports:** Synthesizes structured intelligence reports on specific topics and timeframes, downloadable as CSV.
9. **User Library & Alerting:** Saved searches, bookmarks, notification alert rules (by keyword or category), and multi-channel delivery (in-app inbox, webhooks, SMTP email).
10. **Operations & Observability:** Prometheus metrics (`/metrics`), health probes (`/healthz`, `/readyz`), database migrations, and audit trails.

---

## 2. Tech Stack

| Layer | Technologies & Libraries |
|---|---|
| **Language & Runtime** | Python >= 3.14 (Backend), Node.js >= 18 (Frontend) |
| **Package Management** | `uv` (Python), `npm` (Frontend) |
| **Backend Framework** | FastAPI (0.137+), Starlette, Uvicorn (0.49+) |
| **Database & ORM** | PostgreSQL 15, SQLAlchemy 2.0 (mapped_column declarative style), Alembic (1.18+) |
| **Vector Database** | Qdrant (1.18+) via `qdrant-client` (dense + sparse named vectors) |
| **Task Queue & Cache** | Redis 7, Python-RQ (`rq` 2.10+) with Redis-based TTL locks/markers |
| **Embeddings & Reranking** | `BAAI/bge-m3` via `FlagEmbedding` (1024 dense + sparse), `BAAI/bge-reranker-base` via `sentence-transformers.CrossEncoder` |
| **Clustering Algorithms** | `umap-learn` (0.5.7+), `hdbscan` (0.8.40+), `numpy` |
| **LLM Inference** | Ollama (local server at `http://localhost:11434`), default model: `qwen2.5:3b` |
| **Web Scraping & Extraction** | `httpx`, `feedparser`, `beautifulsoup4`, `langdetect` |
| **Security & Auth** | `bcrypt`, `pyjwt[crypto]`, API keys (`pls_` prefix SHA-256 hashed), double-submit CSRF, SSRF validator |
| **Frontend Framework** | Next.js 16 (App Router, Turbopack), React 19, TypeScript 5 |
| **Styling & UI** | Tailwind CSS v4 (`@theme inline` in `globals.css`), `cmdk` (Command Palette), `framer-motion`, `sonner` (toasts) |
| **Client State & Fetching** | TanStack Query v5 (`@tanstack/react-query`) |
| **Metrics & Ops** | Custom Prometheus text exporter (`/metrics`), Docker & Docker Compose |

---

## 3. Folder & File Structure

```
pulseai/
├── backend/
│   ├── main.py                     # FastAPI app: lifespan, CORS, CSRF, metrics middleware, error envelope
│   ├── core/                       # Shared infrastructure & utilities
│   │   ├── config.py               # Pydantic BaseSettings (reads .env, singleton settings)
│   │   ├── database.py             # SQLAlchemy engine, SessionLocal, get_db, session_scope
│   │   ├── queue.py                # Redis & RQ wiring: ingest, embed, cluster queues, locks, retries
│   │   ├── storage.py              # Object storage abstraction: LocalObjectStorage & S3ObjectStorage
│   │   ├── logging.py              # JSON/structured console logging configuration
│   │   ├── ssrf.py                 # Outbound URL validation against private/internal IPs & cloud metadata
│   │   ├── audit.py                # write_audit() non-blocking helper for audit_log table
│   │   └── pagination.py           # Generic Page[T] Pydantic model and paginate() helper
│   ├── db/
│   │   ├── models.py               # All 22 SQLAlchemy 2.0 ORM models
│   │   ├── seed.py                 # seed_reference_data() idempotent reference seeder
│   │   └── seed_data.py            # Taxonomy categories, ISO countries, ISO languages, ranking_configs
│   ├── modules/                    # Business modules (strictly bounded modular monolith)
│   │   ├── api/                    # Top-level API assembly
│   │   │   ├── router.py           # Assembles /api/v1 routes with global rate limiting
│   │   │   ├── health.py           # /healthz, /readyz, /health probes
│   │   │   └── metrics.py          # Prometheus text exposition /metrics & MetricsMiddleware
│   │   ├── auth/                   # Identity, authentication, API keys, RBAC
│   │   │   ├── router.py           # /auth/*, /users/*, /api-keys/*, /usage endpoints
│   │   │   ├── service.py          # User registration, authentication, key generation, principal resolver
│   │   │   ├── security.py         # bcrypt hashing, JWT issuance/verification, SHA-256 token hashes
│   │   │   ├── deps.py             # get_current_user, get_optional_user, require_role, require_scope
│   │   │   ├── ratelimit.py        # Redis sliding-window rate limiter dependency
│   │   │   ├── csrf.py             # Double-submit CSRF middleware
│   │   │   └── schemas.py          # Auth request/response Pydantic models
│   │   ├── ingestion/              # RSS feed fetching, parsing, deduplication, storage
│   │   │   ├── router.py           # /sources/* and /articles/* endpoints
│   │   │   ├── service.py          # Source CRUD, poll_source, ingest_entries, process_article
│   │   │   ├── jobs.py             # RQ jobs: poll_source_job, process_article_job
│   │   │   ├── fetcher.py          # Safe HTTP client with timeout, user-agent, SSRF checks
│   │   │   ├── parser.py           # Feed parsing (feedparser) and HTML content extraction (BS4)
│   │   │   ├── classifier.py       # Rule/keyword category classifier and langdetect wrapper
│   │   │   ├── dedupe.py           # URL normalization, SHA-256 hashing, fuzzy Levenshtein title dedupe
│   │   │   ├── schemas.py          # Source and Article Pydantic schemas
│   │   │   └── seeds.py            # seed_default_sources() (TechCrunch, Verge, Guardian, BBC)
│   │   ├── retrieval/              # Chunking, BGE-M3 embedding, Qdrant search, reranking
│   │   │   ├── router.py           # POST /api/v1/search endpoint
│   │   │   ├── service.py          # embed_article, search (dense/sparse/hybrid), BGE-reranker
│   │   │   ├── chunker.py          # Token-bounded sentence-aware chunker (spec §15)
│   │   │   ├── jobs.py             # RQ job: embed_article_job
│   │   │   └── schemas.py          # SearchRequest, SearchFilters, SearchResult
│   │   ├── ranking/                # Intent detection & temporal score blending
│   │   │   └── service.py          # detect_intent, compute_freshness_score, blend_scores
│   │   ├── events/                 # Event clustering, lifecycle, timeline, notifications
│   │   │   ├── router.py           # /events/*, /events/{id}/timeline, /events/merge
│   │   │   ├── service.py          # match_article_to_event (fast path), cluster_unmatched_articles (slow path), close_stale_events
│   │   │   ├── jobs.py             # RQ job: cluster_article_job
│   │   │   ├── summary.py          # Abstractive event summary generation via Ollama LLM
│   │   │   └── schemas.py          # EventDetail, EventTimelineResponse, etc.
│   │   ├── agents/                 # RAG Chat, Deep-Path Reasoning, Executive Reports
│   │   │   ├── router.py           # /chat (SSE), /reports/*, /conversations/* (integration layer)
│   │   │   ├── service.py          # chat_stream, chat_stream_deep, generate_report, evidence agreement
│   │   │   └── schemas.py          # ChatRequest, ReportRequest, ConversationDetail
│   │   ├── reports/                # Module package placeholder (logic implemented in agents)
│   │   ├── insights/               # Dashboard analytics & trends
│   │   │   ├── router.py           # /insights/stats, /insights/trending, /insights/compare, /insights/articles/{id}
│   │   │   ├── service.py          # Pipeline stats, event momentum calculation, source comparison
│   │   │   └── schemas.py          # StatsResponse, TrendingResponse, ComparisonResponse
│   │   ├── library/                # User-saved personal resources
│   │   │   ├── router.py           # /library/searches, /library/bookmarks, /library/notification-rules, /library/notifications
│   │   │   ├── service.py          # Scoped CRUD for searches, bookmarks, notification rules & deliveries
│   │   │   └── schemas.py          # Pydantic schemas for library resources
│   │   └── notifications/          # Multi-channel notification delivery
│   │       └── service.py          # deliver_event() to in-app inbox, webhook POST, or SMTP email
│   └── workers/                    # Long-running background processes and CLIs
│       ├── worker.py               # pulseai-worker: RQ worker consuming ingest, embed, cluster queues
│       ├── scheduler.py            # pulseai-scheduler: 30s tick polling sources + embedding & event reconciliation
│       └── backfill.py             # pulseai-backfill-embeddings and pulseai-backfill-clusters CLI runners
├── frontend/                       # Next.js 16 Web Application
│   ├── src/
│   │   ├── app/                    # App Router pages
│   │   │   ├── page.tsx            # Root redirect (to /dashboard or /login)
│   │   │   ├── layout.tsx          # Root HTML layout, font setup, theme provider, toasts
│   │   │   ├── globals.css         # Tailwind v4 @theme inline definitions & design tokens
│   │   │   ├── dashboard/page.tsx  # Ingestion stats, 14-day chart, trending events
│   │   │   ├── search/page.tsx     # Semantic/keyword/hybrid search, intent picker, save search
│   │   │   ├── events/page.tsx     # Split-screen event list and detail with day-grouped timeline
│   │   │   ├── chat/page.tsx       # SSE streaming chat, thought process display, evidence citations
│   │   │   ├── reports/page.tsx    # Topic-driven report generator, markdown viewer, CSV export
│   │   │   ├── library/page.tsx    # Saved searches, bookmarks, notification rules, notification inbox
│   │   │   ├── article/[id]/page.tsx # Full article reader with source credibility and event link
│   │   │   ├── admin/page.tsx      # User administration and role management
│   │   │   ├── settings/page.tsx   # API key generation and revocation
│   │   │   ├── login/page.tsx      # User authentication
│   │   │   └── register/page.tsx   # User registration
│   │   ├── components/             # Reusable UI & Layout Components
│   │   │   ├── AuthGuard.tsx       # Client-side protected route wrapper (redirects to /login)
│   │   │   ├── ErrorBoundary.tsx   # React rendering error catcher
│   │   │   ├── ThemeProvider.tsx   # Dark/light theme switcher with localStorage persistence
│   │   │   ├── providers.tsx       # TanStack Query Client provider wrapper
│   │   │   ├── layout/Shell.tsx    # Responsive sidebar navigation, user badge, theme toggle
│   │   │   ├── layout/CommandPalette.tsx # Global Cmd+K / Ctrl+K keyboard navigation
│   │   │   └── ui/Toast.tsx        # Toast notification rendering
│   │   └── lib/
│   │       ├── api.ts              # Strongly typed API client for all backend endpoints
│   │       └── utils.ts            # Tailwind clsx/twMerge utility
│   ├── package.json
│   └── Dockerfile
├── migrations/                     # Alembic database migrations
│   ├── env.py                      # Alembic execution environment wired to backend.db.models
│   └── versions/                   # Migration revision scripts (Phase 1 to Phase 7)
├── ops/                            # Observability configuration
│   ├── prometheus.yml              # Prometheus scrape configuration targeting :8090/metrics
│   └── grafana-datasource.yml      # Grafana datasource configuration
├── tests/                          # Automated test suite
│   ├── conftest.py                 # Test database (pulseai_test), auto-truncation fixtures, mock settings
│   ├── unit/                       # Unit tests (chunker, dedupe, classifier, ranking, etc.)
│   ├── integration/                # API integration tests (search, events, chat, auth, sources)
│   └── load/                       # k6 load testing scripts (search.js, events_read.js)
├── .importlinter                   # Import Linter configuration enforcing 4 modular monolith contracts
├── pyproject.toml                  # Python package configuration, dependencies, ruff, pytest settings
├── docker-compose.yml              # Development Docker stack (Postgres, Qdrant, Redis, API, Worker, Scheduler)
├── docker-compose.prod.yml         # Production Docker stack (hardened network, migrate, backup, Grafana, Prom)
├── Dockerfile                      # Backend container definition
└── AGENTS.md                       # Critical agent learnings and architecture rules
```

---

## 4. Architecture & Data Flow

```
                      ┌───────────────────────────────────────────────┐
                      │              RSS News Sources                 │
                      └──────────────────────┬────────────────────────┘
                                             │ Outbound HTTP (SSRF Guard)
                                             ▼
┌──────────────────┐               ┌──────────────────┐
│ pulseai-scheduler│──(30s tick)──►│   Redis Queues   │
└──────────────────┘               └────────┬─────────┘
                                            │
                                            ▼
                                   ┌──────────────────┐
                                   │  pulseai-worker  │
                                   └────────┬─────────┘
                                            │
         ┌──────────────────────────────────┼──────────────────────────────────┐
         │ Stage 1: Ingest                  │ Stage 2: Embed                   │ Stage 3: Cluster
         ▼                                  ▼                                  ▼
┌──────────────────┐               ┌──────────────────┐               ┌──────────────────┐
│ Fetch & Dedupe   │               │ Chunker & BGE-M3 │               │ Fast Centroid    │
│ Store Body in S3/│               │ Encode (Dense &  │               │ Match or Slow    │
│ Local Storage    │               │ Sparse)          │               │ UMAP+HDBSCAN     │
└────────┬─────────┘               └────────┬─────────┘               └────────┬─────────┘
         │                                  │                                  │
         ▼                                  ▼                                  ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 Storage Systems                                        │
│  - PostgreSQL 15: Articles, Chunks, Events, Users, Auth, Library, Ranking, Reports     │
│  - Object Storage: Raw full-text article bodies (articles/{id}.txt)                     │
│  - Qdrant: pulseai_articles (chunks) + pulseai_event_centroids (running averages)      │
└───────────────────────────────────────────▲────────────────────────────────────────────┘
                                            │
                                            │ Search / Read / Write
                                            ▼
                                   ┌──────────────────┐
                                   │   FastAPI API    │◄────── Ollama LLM (Qwen 2.5:3b)
                                   │  (:8090 / :8000) │
                                   └────────▲─────────┘
                                            │
                                            │ REST / SSE
                                            ▼
                                   ┌──────────────────┐
                                   │ Next.js Frontend │
                                   │     (:3000)      │
                                   └──────────────────┘
```

### The Modular Monolith Rules (`.importlinter`)
The codebase strictly adheres to 4 architectural contracts enforced on every CI run via `uv run lint-imports`:
1. **API is the top layer:** Nothing in the entire codebase may import `backend.modules.api`.
2. **Business modules are independent:** The modules `ingestion`, `retrieval`, `ranking`, `events`, `agents`, `reports`, `insights`, and `library` **never import each other**, with three explicit exceptions:
   - `core.queue` acts as the deferred RQ job-enqueue hub.
   - `retrieval.service` imports pure ranking functions from `ranking.service`.
   - `agents.router` (the router/integration layer) imports `retrieval.service` and injects `search_fn` into `agents.service`. `agents.service` never imports retrieval at runtime.
3. **Auth is shared infrastructure:** Business modules and API routers may import `auth` (for `require_role`, `get_current_user`, etc.).
4. **Auth never imports business modules:** `backend.modules.auth` has zero dependencies on any business logic.

### End-to-End Ingestion Pipeline
1. **Schedule Trigger:** `pulseai-scheduler` runs every `SCHEDULER_TICK_SECONDS` (30s). It queries Postgres for due sources (`last_polled_at + poll_interval <= now`) and sources awaiting retry from exponential backoff whose Redis TTL marker has expired.
2. **Poll Job (`ingest` queue):** Worker executes `poll_source_job(source_id)` with a Redis distributed lock (`poll_lock:{source_id}`).
3. **Fetch & Deduplication:**
   - Outbound URL is checked via `backend.core.ssrf.check_url` to prevent SSRF against private networks and cloud metadata (AWS/GCP/Azure).
   - Feed is parsed with `feedparser`.
   - **Fast Dedupe:** Exact URL match checked via `SHA-256(normalize_url(url))` against `articles.url_hash`.
   - **Fuzzy Dedupe:** Normalized title checked against same-source articles published in a 6-hour window using `SequenceMatcher` ratio >= 0.92.
4. **Article Persistence:** If unique, `Article` is inserted into PostgreSQL.
5. **Process Job (`ingest` queue):** Worker executes `process_article_job(article_id)`:
   - Fetches full HTML article body, extracts text using BeautifulSoup4 (`extract_main_content`).
   - Writes body to Object Storage (`articles/{article_id}.txt`). Inline `content_preview` is trimmed to 500 characters.
   - Detects language (`langdetect`) and classifies category using taxonomy keywords.
   - Enqueues `embed_article_job` to the `embed` queue.

### Embedding & Vectorization Pipeline
1. **Chunking (`embed` queue):** Worker executes `embed_article_job(article_id)`:
   - Formats article text: headline prepended to the body.
   - If <= 300 tokens, remains a single chunk. Otherwise, chunks at 256-token targets with 40-token sentence-aligned overlap.
   - Inserts `ArticleChunk` records in PostgreSQL with status `pending`.
2. **Encoding (BGE-M3):**
   - Encodes chunks using `BGEM3FlagModel` in a single pass, outputting 1024-dimension dense vectors and sparse lexical weights.
3. **Qdrant Upsert:**
   - Upserts points into `pulseai_articles` collection with payload containing article metadata (`article_id`, `chunk_id`, `title`, `source_id`, `credibility_score`, `published_at`, `category_code`, `chunk_text`).
   - Updates `ArticleChunk.embedding_status = "embedded"`.
   - Enqueues `cluster_article_job` to the `cluster` queue.

### Event Clustering Pipeline
1. **Fast-Path Centroid Matching (`cluster` queue):**
   - Computes article vector by taking the mean of its chunk vectors from Qdrant.
   - Queries `pulseai_event_centroids` collection for open events within cosine threshold (`event_match_threshold = 0.72`).
   - If matched: attaches article to event via `event_articles`, increments `article_count`, updates `last_updated`, recalculates the centroid vector as a running average, and updates Qdrant.
2. **Slow-Path Batch Clustering (Scheduler / Backfill):**
   - Every 30 minutes (`EVENT_SLOW_PATH_INTERVAL_MINUTES`), scheduler invokes `reconcile_events()`.
   - Gathers unmatched articles with embedded chunks published in the last 6 hours (`EVENT_SLOW_PATH_WINDOW_HOURS`).
   - If count >= 3 (`EVENT_MIN_CLUSTER_SIZE`), applies UMAP (reducing to 5 dimensions) followed by HDBSCAN.
   - Each resulting cluster creates an `Event` row:
     - Title and extractive preview chosen from the most central member article.
     - Centroid vector calculated and stored in `pulseai_event_centroids`.
     - Ollama LLM (`qwen2.5:3b`) generates an abstractive summary.
     - Evaluates active `notification_rules` and triggers deliveries.
3. **Event Closure:**
   - Events with no new articles for 72 hours (`EVENT_CLOSE_HOURS`) are marked `status = 'closed'` and their centroids are deleted from Qdrant.

### Multi-Stage Search & Ranking Pipeline
When `POST /api/v1/search` is called:
1. **Retrieval Stage:** Query is encoded with BGE-M3 (dense + sparse). Depending on `mode`:
   - `semantic`: Queries `dense` vector in Qdrant.
   - `keyword`: Queries `sparse` vector in Qdrant.
   - `hybrid`: Executes prefetch for both dense and sparse, fused using Reciprocal Rank Fusion (`FusionQuery(fusion=Fusion.RRF)`).
   - Filters (date range, category, source, language, event) applied natively at Qdrant payload level.
2. **Chunk-to-Article Deduplication:** Top hits are grouped by `article_id`, keeping each article's best chunk score.
3. **Cross-Encoder Rerank (FR-13):** If `rerank_enabled = True`, the top-50 candidates are scored with `BAAI/bge-reranker-base` on `(query, chunk_text)` pairs and sorted.
4. **Intent-Aware Temporal Ranking (FR-14/15):**
   - Query intent is detected:
     - `recency`: Triggers on keywords like "latest", "breaking", "today", "update".
     - `historical`: Triggers on "history", "origin", "evolution", specific 4-digit years.
     - `default`: General queries.
   - Weights fetched from `ranking_configs` table in DB:
     - Recency: $w_{\text{sim}}=0.35, w_{\text{fresh}}=0.40, w_{\text{cred}}=0.15, w_{\text{event}}=0.10$
     - Default: $w_{\text{sim}}=0.55, w_{\text{fresh}}=0.20, w_{\text{cred}}=0.15, w_{\text{event}}=0.10$
     - Historical: $w_{\text{sim}}=0.70, w_{\text{fresh}}=0.05, w_{\text{cred}}=0.15, w_{\text{event}}=0.10$
   - Score calculated:
     $$\text{Blended} = w_{\text{sim}} \cdot \text{sim} + w_{\text{fresh}} \cdot e^{-\ln(2)\frac{\text{age\_days}}{7}} + w_{\text{cred}} \cdot \text{cred} + w_{\text{event}} \cdot \mathbf{1}_{\{\text{in\_event}\}}$$
   - Top `limit` articles returned.

---

## 5. Important Files & Their Responsibilities

### Core Infrastructure
- `backend/core/config.py`: Single source of truth for configuration. Defines `Settings` with Pydantic settings loading from `.env`.
- `backend/core/database.py`: SQLAlchemy database engine, session factory (`SessionLocal`), request-scoped `get_db` dependency, and worker `session_scope()`.
- `backend/core/queue.py`: RQ queues (`ingest`, `embed`, `cluster`), atomic Redis poll locks, retry timers for Windows/POSIX compatibility, and job enqueue dispatchers.
- `backend/core/storage.py`: Storage protocol and implementations: `LocalObjectStorage` (path-traversal-safe) and `S3ObjectStorage` (Boto3).
- `backend/core/ssrf.py`: Resolves target hosts to IP addresses, ensuring outbound requests never hit private subnets (RFC 1918), loopback, link-local, or cloud metadata services.
- `backend/core/audit.py`: Non-blocking, exception-swallowing `write_audit()` logger into `audit_log`.

### Database & Models
- `backend/db/models.py`: Complete database schema declaring 22 SQLAlchemy models with table arguments, check constraints, indexes, and cascades.
- `backend/db/seed.py`: Idempotent seeder run during application lifespan to populate categories, countries, languages, and ranking weights.

### Application Entrypoints & Modules
- `backend/main.py`: FastAPI initialization, lifespan management, CORS, CSRF middleware, metrics middleware, global error envelope handlers, and CLI runner.
- `backend/modules/api/router.py`: Aggregates all `/api/v1` routers and enforces the global Redis sliding-window rate limiter.
- `backend/modules/api/metrics.py`: Custom Prometheus text exporter providing request counters, latency histograms, queue depths, and infra health.
- `backend/modules/auth/service.py`: Password hashing, JWT token generation, refresh token rotation, API key generation, and principal resolution.
- `backend/modules/auth/deps.py`: FastAPI security dependencies (`get_current_user`, `require_role`, `require_scope`).
- `backend/modules/ingestion/service.py`: Feed fetching, duplicate detection, article insertion, content scraping, and source health tracking.
- `backend/modules/retrieval/service.py`: Text chunking, BGE-M3 embedding, Qdrant vector operations, cross-encoder reranking, and search orchestration.
- `backend/modules/retrieval/chunker.py`: Sentence-aware, token-budgeted text chunking algorithm.
- `backend/modules/ranking/service.py`: Pure scoring functions for intent heuristic detection, exponential decay, and score blending.
- `backend/modules/events/service.py`: Centroid matching (fast path), UMAP + HDBSCAN clustering (slow path), event closure, and notification rule matching.
- `backend/modules/events/summary.py`: Calls local Ollama LLM to synthesize abstractive event summaries.
- `backend/modules/agents/router.py`: Integration point importing `retrieval.service` and injecting `search_fn` into agent operations.
- `backend/modules/agents/service.py`: Streaming fast-path chat, multi-step deep-path reasoning (planner/reasoner/synthesizer), lexical evidence agreement scoring, and LLM token usage tracking.
- `backend/modules/insights/service.py`: Aggregate pipeline statistics, event momentum calculations (rising/steady/cooling), and cross-source comparative analysis.
- `backend/modules/library/service.py`: Scoped user queries for bookmarks, saved searches, and notification deliveries.
- `backend/modules/notifications/service.py`: Multi-channel event dispatcher: in-app notifications, external webhook POSTs, and SMTP emails.

### Workers & CLIs
- `backend/workers/worker.py`: RQ worker process. Automatically uses `SimpleWorker` on Windows (avoiding `os.fork`) and standard `Worker` on POSIX.
- `backend/workers/scheduler.py`: 30-second interval scheduler running source poll checks and periodic reconciliations.
- `backend/workers/backfill.py`: One-shot CLIs and reconcile functions: `pulseai-backfill-embeddings` and `pulseai-backfill-clusters`.

---

## 6. Features & Workflows

### 1. Ingestion & Polling Workflow
- **Polite Crawling:** Per-source `poll_interval_minutes` enforced (minimum 5 minutes).
- **Failure Handling & Degraded Sources:**
  - 1st failure: retry scheduled in 1 minute.
  - 2nd failure: retry scheduled in 5 minutes.
  - 3rd failure: retry scheduled in 30 minutes.
  - 4th+ failure: source marked `degraded` and audit log generated.

### 2. Search & Intent-Based Ranking
- **Modes:** `semantic` (dense cosine similarity), `keyword` (sparse lexical matching), `hybrid` (Reciprocal Rank Fusion).
- **Filters:** Date range, source, category, country, language, event membership.
- **Reranker:** Cross-encoder scores query against chunk text, surfacing semantically coherent passages.
- **Temporal Blending:** Dynamically adjusts ranking weights based on detected intent (e.g., boosting freshness for breaking news).

### 3. Events & Topic Tracking
- **Clustering:** Connects disparate articles covering the same story across different sources and dates.
- **Timeline:** Groups articles by day, extracting key daily headlines and top distinctive keywords.
- **Event Merging:** Admins can merge duplicate events via `POST /api/v1/events/merge`.

### 4. Interactive RAG Chat
- **Fast Path:** For questions <= 30 words without comparative clauses. Retrieves 5 relevant articles, feeds context to Ollama, and streams response tokens via SSE.
- **Deep Path:** Automatically activated for complex queries (>30 words or containing "compare", "analyze", "why", "difference between").
  1. *Planner:* Breaks user query into 2-4 sub-questions.
  2. *Retriever:* Performs separate searches for each sub-question.
  3. *Reasoner:* Generates grounded answers with citations for each sub-question.
  4. *Synthesizer:* Merges partial answers into an executive analytical answer.
- **Evidence Citations:** Inline references `[#1]`, `[#2]` linked to article metadata.
- **Evidence Agreement:** Computes pairwise Jaccard title overlap across cited sources to determine mutual support (0.0 to 1.0).

### 5. Executive Reports
- Generates structured analytical intelligence reports on any topic with evidence citations.
- Available for download in CSV format via `GET /api/v1/reports/{id}/export`.

### 6. User Library & Alerting
- **Bookmarks:** Save articles for later reading.
- **Saved Searches:** Re-run complex queries and filters in one click.
- **Alert Rules:** Trigger when new events match a keyword or category.
- **Multi-Channel Delivery:** In-app inbox, HTTP webhook POST, or SMTP email.

---

## 7. APIs, Routes & Integrations

All API responses strictly adhere to the standard error envelope on failure:
```json
{
  "error": {
    "code": "BAD_REQUEST | UNAUTHORIZED | FORBIDDEN | NOT_FOUND | CONFLICT | VALIDATION_ERROR | RATE_LIMITED | INTERNAL_ERROR | SERVICE_UNAVAILABLE",
    "message": "Human-readable description",
    "request_id": "uuid-or-header-value"
  }
}
```
All paginated list endpoints return:
```json
{
  "items": [...],
  "page": 1,
  "page_size": 20,
  "total": 100
}
```

### Complete Endpoint Reference

| Method | Path | Auth / Role | Description |
|---|---|---|---|
| **System & Ops** | | | |
| `GET` | `/healthz` | Open | Liveness probe (checks process is running) |
| `GET` | `/readyz` | Open | Readiness probe (verifies Postgres, Qdrant, Redis connectivity) |
| `GET` | `/health` | Open | Legacy health check alias |
| `GET` | `/metrics` | Open | Prometheus metrics exposition |
| **Authentication & Users** | | | |
| `POST` | `/api/v1/auth/register` | Open | Create local account (assigns `user` role) |
| `POST` | `/api/v1/auth/login` | Open | Returns access JWT + sets httpOnly refresh & CSRF cookies |
| `POST` | `/api/v1/auth/session` | Open | Exchanges third-party JWT (Clerk/Auth0) for session tokens |
| `POST` | `/api/v1/auth/refresh` | Cookie | Rotates refresh token and returns new access JWT |
| `POST` | `/api/v1/auth/logout` | Cookie | Revokes refresh token and clears auth cookies |
| `GET` | `/api/v1/users/me` | `user` | Returns profile of current authenticated user |
| `GET` | `/api/v1/users` | `admin` | Paginated list of users |
| `PATCH`| `/api/v1/users/{id}/role` | `admin` | Updates user role (`user`, `analyst`, `admin`) |
| `GET` | `/api/v1/api-keys` | `user` | Lists user's API keys |
| `POST`| `/api/v1/api-keys` | `user` | Creates an API key (`pls_` prefix; raw key shown once) |
| `DELETE`| `/api/v1/api-keys/{id}` | `user` | Revokes an API key |
| `GET` | `/api/v1/usage` | `user` | Token usage summary (admins see all, users see own) |
| **Ingestion & Sources** | | | |
| `GET` | `/api/v1/sources` | `user` | Lists RSS sources with health, interval, and failure status |
| `POST`| `/api/v1/sources` | `admin` | Adds new source (feed is fetched and validated before saving) |
| `PATCH`| `/api/v1/sources/{id}` | `admin` | Updates credibility score, poll interval, status, or URL |
| `POST`| `/api/v1/sources/{id}/poll`| `admin` | Manually enqueues an immediate poll job |
| `GET` | `/api/v1/articles` | Open | Lists articles with filters (date, category, language, etc.) |
| `GET` | `/api/v1/articles/{id}` | Open | Returns single article metadata and preview |
| **Search & Retrieval** | | | |
| `POST`| `/api/v1/search` | Open | Multi-mode search (`semantic`, `keyword`, `hybrid`) with intent ranking |
| **Events** | | | |
| `GET` | `/api/v1/events` | Open | Paginated events list with keyword, confidence, and date filters |
| `GET` | `/api/v1/events/{id}` | Open | Event detail with member articles timeline |
| `GET` | `/api/v1/events/{id}/timeline` | Open | Day-grouped evolving timeline with headlines and keywords |
| `POST`| `/api/v1/events/merge` | `admin` | Merges source event into target event |
| **Chat & Reports** | | | |
| `POST`| `/api/v1/chat` | `user` | SSE stream for RAG chat (routes to fast path or deep path) |
| `GET` | `/api/v1/conversations` | `user` | Lists user's chat conversations |
| `GET` | `/api/v1/conversations/{id}` | `user` | Full conversation message history with citations |
| `POST`| `/api/v1/reports/generate` | `analyst` | Generates executive intelligence report |
| `GET` | `/api/v1/reports` | `analyst` | Lists generated reports |
| `GET` | `/api/v1/reports/{id}` | `analyst` | Gets single report detail |
| `GET` | `/api/v1/reports/{id}/export` | `analyst` | Exports report as CSV download |
| **Insights** | | | |
| `GET` | `/api/v1/insights/stats` | Open | Pipeline statistics (totals, 14-day chart, top categories) |
| `GET` | `/api/v1/insights/trending` | Open | Events ranked by coverage momentum (rising/steady/cooling) |
| `GET` | `/api/v1/insights/compare` | Open | Cross-source coverage comparison for topic `q` between outlets `a` and `b` |
| `GET` | `/api/v1/insights/articles/{id}` | Open | Detailed article view with source credibility and bookmark status |
| **User Library** | | | |
| `GET` | `/api/v1/library/searches` | `user` | Lists user's saved searches |
| `POST`| `/api/v1/library/searches` | `user` | Saves a search query and filter set |
| `DELETE`| `/api/v1/library/searches/{id}` | `user` | Deletes a saved search |
| `GET` | `/api/v1/library/bookmarks` | `user` | Lists user's bookmarked articles |
| `PUT` | `/api/v1/library/bookmarks/{article_id}` | `user` | Adds article bookmark |
| `DELETE`| `/api/v1/library/bookmarks/{article_id}` | `user` | Removes article bookmark |
| `GET` | `/api/v1/library/notification-rules` | `user` | Lists user's event alert rules |
| `POST`| `/api/v1/library/notification-rules` | `user` | Creates alert rule (keyword, category, channel) |
| `DELETE`| `/api/v1/library/notification-rules/{id}` | `user` | Deletes an alert rule |
| `GET` | `/api/v1/library/notifications` | `user` | In-app notification inbox with unread count |
| `POST`| `/api/v1/library/notifications/read-all` | `user` | Marks all in-app notifications as read |

---

## 8. Database Schema & Models

All 22 PostgreSQL tables are declared in `backend/db/models.py`.

```
┌────────────────────────────────────────────────────────────────────────┐
│                              TAXONOMY & CONFIG                         │
├─────────────────┬─────────────────┬─────────────────┬──────────────────┤
│ categories      │ countries       │ languages       │ ranking_configs  │
│ - code (PK)     │ - code (PK)     │ - code (PK)     │ - intent (PK)    │
│ - label         │ - name          │ - name          │ - w_sim          │
│                 │                 │                 │ - w_fresh        │
│                 │                 │                 │ - w_cred         │
│                 │                 │                 │ - w_event        │
└─────────────────┴─────────────────┴─────────────────┴──────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              INGESTION & CORPUS                        │
├───────────────────────────────┬────────────────────────────────────────┤
│ sources                       │ articles                               │
│ - id (UUID PK)                │ - id (UUID PK)                         │
│ - name                        │ - source_id (FK -> sources.id)         │
│ - rss_url                     │ - title, author, description           │
│ - website                     │ - url, url_hash (UNIQUE SHA-256)       │
│ - credibility_score (0..1)    │ - content_ref (storage key)            │
│ - credibility_method          │ - content_preview (< 500 chars)        │
│ - category_code (FK)          │ - image_url                            │
│ - status (active/degraded/...)│ - language_code, category_code (FKs)   │
│ - poll_interval_minutes (>=5) │ - published_at, processed_at           │
│ - last_polled_at              │ - event_id (FK -> events.id)           │
│ - consecutive_failures        │ - created_at                           │
├───────────────────────────────┴────────────────────────────────────────┤
│ article_chunks                                                         │
│ - id (UUID PK)                                                         │
│ - article_id (FK -> articles.id CASCADE)                               │
│ - chunk_number                                                         │
│ - chunk_text                                                           │
│ - token_count                                                          │
│ - qdrant_point_id (UUID)                                               │
│ - embedding_status (pending/embedded/failed)                           │
└────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              EVENTS                                    │
├───────────────────────────────┬────────────────────────────────────────┤
│ events                        │ event_articles                         │
│ - id (UUID PK)                │ - event_id (FK -> events.id CASCADE)   │
│ - title                       │ - article_id (FK -> articles.id CASCADE│
│ - summary                     │ - similarity_at_match                  │
│ - confidence (0..1)           │ - added_at                             │
│ - status (open/closed/merged) │   (Composite PK: event_id, article_id) │
│ - centroid_vector_id (UUID)   │                                        │
│ - article_count               │                                        │
│ - created_at, last_updated    │                                        │
└───────────────────────────────┴────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              IDENTITY & SECURITY                       │
├───────────────────────────────┬───────────────────┬────────────────────┤
│ users                         │ refresh_tokens    │ api_keys           │
│ - id (UUID PK)                │ - id (UUID PK)    │ - id (UUID PK)     │
│ - email (UNIQUE)              │ - user_id (FK)    │ - user_id (FK)     │
│ - password_hash               │ - token_hash (UNQ)│ - key_hash (UNIQUE)│
│ - display_name                │ - expires_at      │ - label            │
│ - role (user/analyst/admin)   │ - revoked_at      │ - scopes (ARRAY)   │
│ - is_active                   │ - created_at      │ - last_used_at     │
│ - created_at                  │                   │ - revoked_at       │
└───────────────────────────────┴───────────────────┴────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              CHAT & REASONING                          │
├───────────────────────────────┬────────────────────────────────────────┤
│ conversations                 │ conversation_messages                  │
│ - id (UUID PK)                │ - id (UUID PK)                         │
│ - user_id (FK -> users.id)    │ - conversation_id (FK CASCADE)         │
│ - title                       │ - role (user / assistant)              │
│ - created_at, updated_at      │ - content                              │
│                               │ - evidence (JSONB)                     │
│                               │ - evidence_agreement (Float 0..1)      │
│                               │ - created_at                           │
├───────────────────────────────┼────────────────────────────────────────┤
│ reports                       │ llm_usage                              │
│ - id (UUID PK)                │ - id (BigInt PK)                       │
│ - user_id (FK -> users.id)    │ - user_id (FK -> users.id NULLABLE)    │
│ - topic, timeframe            │ - operation (chat_fast/deep/summary)   │
│ - status (pending/completed)  │ - model (e.g. qwen2.5:3b)              │
│ - content (JSONB)             │ - input_tokens, output_tokens          │
│ - evidence_agreement (JSONB)  │ - latency_ms                           │
│ - created_at                  │ - created_at                           │
└───────────────────────────────┴────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────┐
│                              USER LIBRARY & OPS                        │
├─────────────────┬─────────────────┬─────────────────┬──────────────────┤
│ bookmarks       │ saved_searches  │ saved_reports   │ audit_log        │
│ - user_id (PK)  │ - id (UUID PK)  │ - id (UUID PK)  │ - id (BigInt PK) │
│ - article_id(PK)│ - user_id (FK)  │ - user_id (FK)  │ - user_id (FK)   │
│ - created_at    │ - query         │ - title         │ - action         │
│                 │ - filters(JSONB)│ - content(JSONB)│ - target_type    │
│                 │ - created_at    │ - query         │ - target_id      │
│                 │                 │ - created_at    │ - ip_address     │
│                 │                 │                 │ - metadata(JSONB)│
│                 │                 │                 │ - created_at     │
├─────────────────┴─────────────────┴─────────────────┴──────────────────┤
│ notification_rules                                                     │
│ - id (UUID PK), user_id (FK), keyword_or_topic, category_code (FK),    │
│   channel (email/in_app/webhook), is_active, created_at                │
├────────────────────────────────────────────────────────────────────────┤
│ notification_deliveries                                                │
│ - id (UUID PK), rule_id (FK), user_id (FK), event_id (FK SET NULL),    │
│   channel, status (sent/failed/pending), detail, read_at, created_at   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Authentication & Authorization

### Authentication Modes
Controlled by `AUTH_PROVIDER`:
- `none` (Default): Local user authentication using bcrypt password hashing. Returns HS256 access JWT and sets an `httpOnly` refresh cookie.
- `clerk` / `auth0`: Managed identity provider mode. Validates incoming provider tokens via remote RS256 JWKS endpoints and synchronizes user profiles into the local `users` table.

### Principal Resolution (`backend/modules/auth/deps.py`)
Incoming requests resolve credentials in the following order:
1. `Authorization: Bearer <pulseai_jwt>`
2. `Authorization: Bearer <clerk_or_auth0_jwt>`
3. `Authorization: Bearer pls_<api_key>`
4. `pulseai_access` httpOnly cookie (used by browser clients)

### Role-Based Access Control (RBAC)
Role hierarchy:
$$\text{user} < \text{analyst} < \text{admin}$$
- Applied per route via `require_role("user" | "analyst" | "admin")`.
- `user`: Can view sources, chat, view conversations, manage personal library, bookmarks, saved searches, and notification rules.
- `analyst`: All `user` permissions plus generate executive intelligence reports and export reports as CSV.
- `admin`: All `analyst` permissions plus create/update/poll RSS sources, view all users, modify user roles, merge events, and view system-wide LLM token usage.

### API Keys
- Generated with prefix `pls_` followed by 32 random URL-safe characters.
- Stored as SHA-256 digests in `api_keys`. The plain text key is returned to the user exactly once.
- Scoped to permissions: `read`, `chat`, `reports`. Validated via `require_scope("scope")`.

### Security Mechanisms
- **Rate Limiting:** Redis sliding-window log keyed by IP (anonymous) or user ID (authenticated). Enforced across all `/api/v1` routes.
- **CSRF Protection:** Double-submit CSRF cookie (`pulseai_csrf`) verified against the `X-CSRF-Token` header for cookie-authenticated mutations.
- **SSRF Protection:** Outbound HTTP client (`backend.core.ssrf`) resolves DNS and blocks private, link-local, loopback, and cloud metadata IP ranges.
- **Audit Trail:** Append-only records logged via `write_audit()` for auth events, role updates, and system-level actions.

---

## 10. Environment Variables & Configuration

All environment variables are declared in `backend/core/config.py` using Pydantic Settings.

| Variable | Default Value | Description |
|---|---|---|
| **PostgreSQL** | | |
| `POSTGRES_USER` | *Required* | Database user |
| `POSTGRES_PASSWORD` | *Required* | Database password |
| `POSTGRES_DB` | *Required* | Database name |
| `POSTGRES_HOST` | `localhost` | Database host (Docker maps to `5434` on host) |
| `POSTGRES_PORT` | `5432` | Database port |
| `POSTGRES_DRIVER` | `postgresql` | SQLAlchemy driver (`postgresql` or `postgresql+asyncpg`) |
| **Qdrant** | | |
| `QDRANT_URL` | `http://localhost:6333` | Vector database REST URL |
| `QDRANT_ARTICLES_COLLECTION` | `pulseai_articles` | Chunk vector collection name |
| `QDRANT_EVENT_CENTROIDS_COLLECTION` | `pulseai_event_centroids` | Event centroids collection name |
| `QDRANT_SHARDS` | `2` | Number of collection shards |
| **Redis** | | |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis instance URL |
| **Storage** | | |
| `STORAGE_BACKEND` | `local` | `local` (filesystem) or `s3` |
| `STORAGE_LOCAL_DIR` | `./storage/objects` | Local disk storage directory |
| `STORAGE_S3_BUCKET` | `None` | S3 bucket name |
| `STORAGE_S3_REGION` | `None` | S3 AWS region |
| **Authentication** | | |
| `AUTH_PROVIDER` | `none` | `none`, `clerk`, or `auth0` |
| `JWT_SECRET` | `dev-only-secret-change-me`| Secret key for signing HS256 tokens (>= 32 bytes) |
| `JWT_ISSUER` | `pulseai` | JWT issuer claim |
| `JWT_AUDIENCE` | `pulseai-api` | JWT audience claim |
| `JWT_ACCESS_TTL_MINUTES` | `15` | Access token lifespan in minutes |
| `REFRESH_TTL_DAYS` | `30` | Refresh token lifespan in days |
| `COOKIE_SECURE` | `false` | Set `true` in production behind HTTPS |
| `CSRF_ENABLED` | `true` | Enforce double-submit CSRF protection |
| **Rate Limiting** | | |
| `RATE_LIMIT_ENABLED` | `true` | Enable Redis rate limiting |
| `RATE_LIMIT_ANON_PER_MINUTE` | `30` | Requests per minute for unauthenticated clients |
| `RATE_LIMIT_AUTH_PER_MINUTE` | `120` | Requests per minute for authenticated users |
| `RATE_LIMIT_CHAT_PER_MINUTE` | `10` | Requests per minute for chat/report endpoints |
| **Embeddings & Search** | | |
| `EMBEDDING_MODEL` | `BAAI/bge-m3` | Embedding model identifier |
| `EMBEDDING_SIZE` | `1024` | Dense vector dimension |
| `EMBEDDING_BATCH_SIZE` | `16` | Batch size for embedding worker |
| `CHUNK_TARGET_TOKENS` | `256` | Target tokens per text chunk |
| `CHUNK_OVERLAP_TOKENS` | `40` | Overlap tokens between consecutive chunks |
| `SINGLE_CHUNK_MAX_TOKENS`| `300` | Articles below this token count are not split |
| `RERANKER_MODEL` | `BAAI/bge-reranker-base` | Cross-encoder model identifier |
| `RERANK_ENABLED` | `true` | Enable cross-encoder reranking pass |
| `RERANK_TOP_K` | `50` | Retrieved candidate count fed into reranker |
| `RERANK_TOP_N` | `10` | Final reranked candidate count returned |
| **Events & Clustering** | | |
| `EVENT_MATCH_THRESHOLD` | `0.72` | Fast-path cosine similarity match threshold |
| `EVENT_SLOW_PATH_WINDOW_HOURS` | `6` | Recent article window for slow-path clustering |
| `EVENT_SLOW_PATH_INTERVAL_MINUTES` | `30` | Slow-path execution interval |
| `EVENT_MIN_CLUSTER_SIZE` | `3` | Minimum articles to form an event cluster |
| `EVENT_CLOSE_HOURS` | `72` | Inactivity threshold before closing an event |
| `EVENT_UMAP_COMPONENTS` | `5` | Dimensions for UMAP reduction before HDBSCAN |
| **LLM & Inference** | | |
| `SUMMARY_PROVIDER` | `ollama` | Summary provider (`ollama` or `none`) |
| `SUMMARY_MODEL` | `qwen2.5:3b` | Ollama model for event summaries |
| `CHAT_PROVIDER` | `ollama` | Chat provider (`ollama` or `none`) |
| `CHAT_MODEL` | `qwen2.5:3b` | Ollama model for RAG chat and deep-path reasoning |
| `OLLAMA_URL` | `http://localhost:11434`| Ollama server endpoint |
| `CHAT_NUM_CTX` | `8192` | Max context window tokens for chat model |`n| `CHAT_NUM_PREDICT` | `768` | Max generated tokens per chat turn |`n| `CHAT_CONTEXT_TOKEN_CAP` | `6144` | Soft cap for dynamic context budgeting |`n| `CHAT_MAX_CHUNKS_PER_ARTICLE` | `2` | Max evidence chunks per article in deep path |
| `CHAT_TIMEOUT_SECONDS` | `120` | HTTP timeout for Ollama calls |
| **Ingestion** | | |
| `SEED_DEFAULT_SOURCES` | `true` | Automatically seed demo RSS sources on startup |
| `SCHEDULER_TICK_SECONDS` | `30` | Scheduler polling scan cadence |
| `MIN_POLL_INTERVAL_MINUTES`| `5` | Minimum allowable poll interval |
| `DEFAULT_POLL_INTERVAL_MINUTES` | `15` | Default source polling interval |
| `FEED_FETCH_TIMEOUT_SECONDS` | `15.0` | HTTP timeout when fetching RSS feeds |
| `ARTICLE_FETCH_TIMEOUT_SECONDS` | `10.0` | HTTP timeout when scraping article HTML |
| `FUZZY_DUPLICATE_THRESHOLD`| `0.92` | Title similarity threshold for deduplication |
| `FUZZY_DUPLICATE_WINDOW_HOURS` | `6` | Time window for fuzzy title comparison |
| **Notifications** | | |
| `NOTIFICATION_WEBHOOK_URL`| `None` | Target endpoint for webhook notification rules |
| `SMTP_HOST` | `None` | SMTP server host for email notifications |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` | `None` | SMTP login username |
| `SMTP_PASSWORD` | `None` | SMTP login password |
| `SMTP_FROM` | `pulseai@localhost` | Email sender address |
| **Frontend** | | |
| `NEXT_PUBLIC_API_URL` | `http://127.0.0.1:8090` | Backend API URL reachable by the browser |

---

## 11. Important Business Logic & Algorithms

### 1. Token Estimation & Sentence Chunking (`chunker.py`)
- Approximate token estimation: $\text{tokens} = \text{round}(\text{words} \times 1.3)$.
- Splits on regex `(?<=[.!?])\s+(?=[A-Z0-9"'])` to preserve abbreviations like "U.S.".
- Hard splits words only when a single sentence exceeds `target_tokens`.
- Chunks carry sentence-aligned overlap derived from trailing sentences of the preceding chunk up to `overlap_tokens`.

### 2. Fuzzy Deduplication (`dedupe.py`)
- Fast Path: Compares `SHA-256` hash of cleaned URL (query tracking params like `utm_*` stripped).
- Slow Path: Uses Python's `difflib.SequenceMatcher(None, title1, title2).ratio() >= 0.92` across articles from the same source within 6 hours.

### 3. Fast-Path Centroid Evolution (`events/service.py`)
- Running average formula for event centroid $C_{n+1}$ after article vector $V$ matches:
  $$C_{n+1} = \frac{C_n \cdot n + V}{n + 1}$$
- Centroid update is committed to Qdrant inside the same database transaction.

### 4. Intent Detection Heuristics (`ranking/service.py`)
- Keyword matching determines query intent without invoking an LLM:
  - Recency regex matches: `breaking`, `latest`, `just now`, `today`, `yesterday`, `this week`, `recent`, `current`, `newest`, `update`, `news`, `live`, `happening`.
  - Historical regex matches: `history`, `origin of`, `timeline of`, `evolution of`, `first ever`, `founded`, `since \d{4}`, `archive`, explicit 4-digit years.

### 5. Freshness Decay Formula (`ranking/service.py`)
- Exponential decay using a 7-day half-life:
  $$\text{Freshness} = \exp\left(-\ln(2) \cdot \frac{\text{age\_in\_days}}{7.0}\right)$$
- Articles with null `published_at` default to a score of `0.05`.

### 6. Evidence Agreement Metric (`agents/service.py`)
- Lexical agreement proxy: extracts non-stopwords from cited article titles.
- Pairwise title overlap is measured using Jaccard similarity:
  $$J(T_i, T_j) = \frac{|T_i \cap T_j|}{|T_i \cup T_j|}$$
- If $J(T_i, T_j) \ge 0.10$, articles $i$ and $j$ are marked as mutually supportive.
- Overall score is the fraction of citations with at least one supporter.

---

## 12. How to Run, Build, Test & Deploy

### Prerequisites
- Docker (for PostgreSQL, Qdrant, Redis)
- `uv` (Python package manager)
- Node.js 18+ & npm
- Ollama (installed locally and running `ollama serve`)

### Local Development Setup

```bash
# 1. Start infrastructure services
docker compose up -d postgres qdrant redis

# 2. Setup backend
cp .env.example .env
# Edit .env to set a strong JWT_SECRET (>= 32 bytes):
# python -c "import secrets; print(secrets.token_urlsafe(48))"
uv sync
uv run alembic upgrade head

# 3. Pull required Ollama model
ollama pull qwen2.5:3b

# 4. Start backend API (Port 8090 on Windows)
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090

# 5. Start background worker (separate terminal)
uv run pulseai-worker

# 6. Start scheduler (separate terminal)
uv run pulseai-scheduler

# 7. Optional: Run manual corpus backfills
uv run pulseai-backfill-embeddings
uv run pulseai-backfill-clusters

# 8. Setup & start frontend (separate terminal)
cd frontend
npm install
npm run dev
# Frontend runs on http://localhost:3000
```

### Running Tests & Linting

```bash
# Run backend test suite (requires docker-compose up for postgres/redis/qdrant)
uv run pytest

# Check coverage with 80% gate
uv run pytest --cov=backend --cov-report=term-missing --cov-fail-under=80

# Linting and formatting checks
uv run ruff check .
uv run ruff format --check .

# Enforce modular monolith architectural boundaries
uv run lint-imports

# Frontend linting
cd frontend && npm run lint
```

### Production Deployment
The hardened production stack is defined in `docker-compose.prod.yml`:
- Isolates PostgreSQL, Qdrant, and Redis behind internal Docker networks (no exposed host ports).
- Includes one-shot `migrate` service running `alembic upgrade head`.
- Automated nightly `pg_dump` backup service with 14-day retention.
- Bundled Prometheus and Grafana instances configured to scrape `/metrics`.
- Production build of Next.js frontend running standalone.

```bash
# Start production stack
docker compose -f docker-compose.prod.yml up -d --build
```

---

## 13. Important Conventions & Rules

1. **Modular Monolith Boundaries:**
   - Never import between sibling business modules (`ingestion`, `retrieval`, `ranking`, `events`, `agents`, `insights`, `library`).
   - Use `backend.core.queue` for background job handoffs.
   - Inject cross-module callables via the router layer (e.g., `agents/router.py` injecting `search_fn` into `agents/service.py`).
2. **FastAPI Sync Endpoints:**
   - Endpoints performing synchronous blocking operations (such as Qdrant queries, BGE-M3 model inference, or cross-encoder reranking) must be declared as regular `def` (not `async def`). FastAPI will execute them in an internal threadpool, preventing event loop starvation.
3. **Async / Sync Crossing:**
   - In `backend/modules/agents/service.py`, report generation runs `asyncio.run()` within a sync endpoint. This works because FastAPI executes sync endpoints on separate worker threads.
4. **Ollama Thinking Models:**
   - Always pass `"think": false` in JSON request payloads when calling Ollama models (such as Qwen 3.5). Otherwise, internal reasoning tokens will consume the entire output token limit before generating a response.
5. **Windows OS Adaptations:**
   - Port 8000 is frequently reserved by Hyper-V on Windows. The local development environment uses port `8090` for the backend API.
   - Python-RQ forks processes using `os.fork` on POSIX systems, which is unsupported on Windows. `backend/workers/worker.py` dynamically selects `SimpleWorker` on Windows (`os.name == "nt"`).
   - RQ's built-in delayed job scheduler requires POSIX. PulseAI uses Redis key expiration TTLs (`retry:{source_id}`, `reconcile:embeddings`) polled by `pulseai-scheduler` to ensure seamless operation on both Windows and Linux.
6. **Database Migration Rules:**
   - Alembic migrations must be reviewed or manually written.
   - Table constraints use the prefix convention `ck_%(table_name)s_%(constraint_name)s`. When adding constraints in migrations, avoid double-prefixing.
   - Any new database table that stores user data or can be mutated during tests must be added to `_MUTABLE_TABLES` in `tests/conftest.py` to ensure clean test isolation.
7. **Frontend Design Tokens & Styling:**
   - Uses Tailwind CSS v4 CSS-first theming (`@theme inline` in `frontend/src/app/globals.css`). Do not create or expect a `tailwind.config.js`.
   - Dark mode is default, with light mode active via `html[data-theme="light"]`.
   - Access tokens are stored in `localStorage` under `pulseai_token`. Guard access with `typeof window !== "undefined"`.
   - SSE streaming chunks in `api.ts` must split on `\n\n` (LF only).

---

## 14. Known Issues, Limitations & Future Roadmap

- **Model Cold Start:** First search or chat call lazily downloads/loads BGE-M3 (~2.3 GB) and BGE-reranker-base (~1.1 GB) into memory, taking 15–30 seconds. Subsequent queries complete in sub-second time.
- **Single-Node Queue:** Redis RQ is designed for single-node task processing. High-throughput distributed scaling would require Celery or Kafka.
- **WebSocket Push (Planned):** Events currently refresh via client polling. A real-time WebSocket push stream for live event updates is on the project roadmap.
- **TLS Termination:** The production compose file intentionally omits TLS; a reverse proxy (e.g., Caddy, Nginx, or Cloudflare Tunnel) should terminate SSL in front of ports 8000 and 3000.
- **Cross-Source Comparison:** `compare_sources` currently requires matching search results across both outlets; sparse news coverage on niche topics may return insufficient comparative data.

