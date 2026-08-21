# PulseAI

Real-time AI news intelligence platform. Ingests global news streams, deduplicates and classifies articles, embeds them for semantic search, groups coverage into evolving events, and generates summaries via a local LLM.

## What it does

1. **Ingests** news from RSS feeds on a configurable schedule
2. **Deduplicates** articles by URL and fuzzy title matching
3. **Classifies** articles by language and category
4. **Embeds** article chunks for semantic search (BGE-M3 dense + sparse)
5. **Searches** across all articles (semantic, keyword, or hybrid mode)
6. **Clusters** articles into events — groups of stories about the same topic
7. **Ranks** results by intent (recency, relevance, or historical)
8. **Chats** with your corpus — ask questions, get cited answers via LLM
9. **Generates reports** — executive intelligence summaries on any topic

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2, Alembic |
| Database | PostgreSQL 15, Redis 7 (RQ queues), Qdrant (vectors) |
| Embeddings | BGE-M3 (dense + sparse), BGE-reranker (cross-encoder) |
| Clustering | UMAP + HDBSCAN (event detection) |
| LLM | Ollama (Qwen 2.5:3b for chat and summaries) |
| Frontend | Next.js 16, React 19, Tailwind CSS v4, TanStack Query |
| Auth | JWT + refresh tokens, API keys, RBAC (user/analyst/admin) |
| CI | GitHub Actions — ruff, import-linter, pytest |

## Quick start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) (for Postgres, Qdrant, Redis)
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- [Node.js](https://nodejs.org/) 18+ (for frontend)
- [Ollama](https://ollama.ai/) (for LLM chat and summaries)

### 1. Start infrastructure

```bash
docker compose up -d postgres qdrant redis
```

### 2. Set up backend

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090
```

### 3. Seed articles and create events

```bash
uv run pulseai-backfill-embeddings
uv run pulseai-backfill-clusters
```

### 4. Start frontend

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) and register an account.

### 5. Start Ollama (for chat)

```bash
ollama serve
```

## Architecture

```
RSS Feeds ──► Scheduler ──► Worker ──► PostgreSQL + Qdrant
                                      │
FastAPI API ◄─────────────────────────┘
     │
     ├── /search    (semantic/keyword/hybrid)
     ├── /events    (clustered stories + timeline)
     ├── /chat      (SSE streaming with citations)
     ├── /reports   (executive summaries)
     └── /admin     (user/role management)

Next.js Frontend ──► FastAPI API
```

The backend is a **modular monolith** — each concern lives in its own module under `backend/modules/`, and import-linter enforces that modules don't import each other. The frontend is a standalone Next.js app that talks to the backend API.

## API overview

| Endpoint | Purpose | Auth |
|---|---|---|
| `POST /api/v1/auth/register` | Create account | open |
| `POST /api/v1/auth/login` | Get access token | open |
| `POST /api/v1/search` | Search articles | open |
| `GET /api/v1/events` | List events | open |
| `GET /api/v1/events/{id}` | Event detail + timeline | open |
| `POST /api/v1/chat` | Chat with corpus (SSE) | user |
| `POST /api/v1/reports/generate` | Generate report | analyst |
| `GET /api/v1/users` | List users | admin |
| `POST /api/v1/events/merge` | Merge events | admin |

Full API docs at [http://localhost:8090/docs](http://localhost:8090/docs) when the backend is running.

## Documentation

- [`DEVELOPER.md`](DEVELOPER.md) — Full developer reference (architecture, setup, config, schema, workflows, testing)
- [`PROJECT_STATUS_AND_ROADMAP.md`](PROJECT_STATUS_AND_ROADMAP.md) — Completion status and what's next
- [`frontend/README.md`](frontend/README.md) — Frontend architecture and features

## License

Private project.
