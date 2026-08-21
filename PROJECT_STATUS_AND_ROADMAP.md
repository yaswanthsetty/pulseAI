# PulseAI — Project Status & Roadmap

> Last updated: August 21, 2026

## Status

| Phase | Description | Status |
|---|---|---|
| Phase 1 | Core infra, ingestion, schema | Complete |
| Phase 1.5 | Auth & RBAC | Complete |
| Phase 2 | Embeddings & search | Complete |
| Phase 3 | Event clustering & summaries | Complete |
| Phase 4 | Temporal ranking | Complete |
| Phase 5 | Chat & reports | Complete |
| Phase 6 | Frontend | Complete |
| Phase 7 | Hardening | In progress |

**Overall: Phases 1-6 complete. Phase 7 (hardening) remaining.**

---

## What's built

### Backend (Python / FastAPI)

**21 database tables**, **5 router modules**, **26 API endpoints**:

- **Ingestion**: RSS polling, deduplication (exact + fuzzy), HTML cleaning, language detection, category classification
- **Auth**: Local + managed provider (Clerk/Auth0), JWT + refresh tokens, API keys, RBAC, rate limiting, CSRF, audit logging
- **Retrieval**: BGE-M3 dense+sparse embeddings, semantic/keyword/hybrid search, cross-encoder reranking
- **Events**: Fast-path centroid matching, UMAP+HDBSCAN clustering, event closure, keyword search, merge API, notification rules
- **Ranking**: Intent detection, freshness decay, credibility scoring, event signal blending
- **Agents**: Fast-path SSE chat, deep-path multi-step reasoning, evidence agreement, report generation
- **LLM**: Ollama integration (Qwen 2.5:3b) for chat and abstractive event summaries

### Frontend (Next.js / React)

**9 pages**, **10 routes**, Kimi-inspired dark UI:

- Login/Register with validation and password toggle
- Search with semantic/keyword/hybrid modes and intent selectors
- Events with split-panel list + timeline detail view
- Chat with SSE streaming, thinking indicators, evidence citations
- Reports with topic input and full content viewer
- Admin panel with user list and role management
- Settings page with API key create/revoke
- Command palette (Cmd+K) for quick navigation
- Mobile-responsive sidebar with hamburger menu
- AuthGuard, error boundary, toast notifications

### Infrastructure

- Docker Compose (Postgres, Qdrant, Redis)
- GitHub Actions CI (ruff, import-linter, pytest)
- Import-linter module boundary enforcement
- 80% test coverage gate

---

## What's left (Phase 7 — Hardening)

| Task | Priority | Effort |
|---|---|---|
| Staging deploy config | High | Medium |
| Load testing (k6/Locust) | High | Medium |
| PostgreSQL backup cron | Medium | Low |
| Prometheus + Grafana monitoring | Medium | Medium |
| Security audit (OWASP checklist) | Medium | Low |
| Dependency audit (`pip-audit`) | Low | Low |

---

## How to run

```bash
# 1. Infrastructure
docker compose up -d postgres qdrant redis

# 2. Backend
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8090

# 3. Seed data (if needed)
uv run pulseai-backfill-embeddings
uv run pulseai-backfill-clusters

# 4. Frontend
cd frontend && npm install && npm run dev

# 5. Ollama (for chat)
ollama serve
```
