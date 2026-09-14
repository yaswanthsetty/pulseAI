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

**22 database tables**, **7 router modules**, **44 API endpoints**:

- **Ingestion**: RSS polling on a schedule, deduplication (exact + fuzzy), HTML cleaning, language detection, category classification
- **Auth**: Local + managed provider (Clerk/Auth0), JWT + refresh tokens, API keys, RBAC, rate limiting, CSRF, audit logging
- **Retrieval**: BGE-M3 dense+sparse embeddings, semantic/keyword/hybrid search, cross-encoder reranking
- **Events**: Fast-path centroid matching, UMAP+HDBSCAN clustering, event closure, keyword search, merge API
- **Notifications**: rule matching → delivery (in-app inbox, webhook, email) with per-attempt delivery records
- **Library**: saved searches, bookmarks, alert rules, notification inbox (all owner-scoped)
- **Insights**: dashboard stats, article detail, trend detection (event momentum), cross-source comparison
- **Ranking**: Intent detection, freshness decay, credibility scoring, event signal blending
- **Agents**: Fast-path SSE chat, deep-path multi-step reasoning, evidence agreement, report generation, report CSV export
- **LLM**: Ollama integration (Qwen) for chat and abstractive event summaries
- **Ops**: Prometheus `/metrics` endpoint (requests, latency, infra health, queue depth)

### Frontend (Next.js / React)

**12 pages**, **13 routes**, Kimi-inspired dark UI with light-mode support:

- Dashboard with pipeline stats, 14-day ingestion chart, trending events
- Login/Register with validation and password toggle
- Search with semantic/keyword/hybrid modes, intent selectors, save-search
- Events with split-panel list + timeline detail view
- Article detail view with bookmark toggle, source credibility, event link
- Library page: saved searches, bookmarks, alert rules, notification inbox
- Chat with SSE streaming, thinking indicators, evidence citations
- Reports with topic input, full content viewer, CSV export
- Admin panel with user list and role management
- Settings page with API key create/revoke
- Command palette (Cmd+K), dark/light toggle, mobile-responsive sidebar
- AuthGuard, error boundary, toast notifications

### Infrastructure

- Docker Compose (Postgres, Qdrant, Redis) + production compose with frontend image, migrate runner, nightly backups, Prometheus + Grafana
- GitHub Actions CI (ruff, import-linter, pytest, pip-audit, npm audit)
- Import-linter module boundary enforcement
- 80% test coverage gate (42 tests across the new Phase 7 modules)
- k6 load-test scripts for search and read endpoints

---

## What's left (beyond the original spec)

| Task | Priority | Effort |
|---|---|---|
| TLS termination / reverse proxy in front of prod compose | High | Low |
| WebSocket push for live event feed | Medium | Medium |
| OWASP pentest pass on auth/ingestion | Medium | Medium |
| k6 baseline numbers recorded + perf tuning (model preload, GPU) | Medium | Medium |

Note: production deployments require a 32+ byte `JWT_SECRET`
(`python -c "import secrets; print(secrets.token_urlsafe(48))"`) — PyJWT warns
on shorter HS256 keys.

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

### Production deployment

```bash
# Requires POSTGRES_PASSWORD, JWT_SECRET (32+ bytes), GRAFANA_PASSWORD in .env
docker compose -f docker-compose.prod.yml up -d --build
# API on :8000, frontend on :3000, Grafana on :3001, Prometheus on :9090
# Nightly DB dumps land in ./backups (14-day retention)
```
