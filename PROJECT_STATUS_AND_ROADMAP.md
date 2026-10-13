# PulseAI — Project Status & Roadmap

> Last updated: October 13, 2026 (verified against commit `5aef4d3`)

## Status — original build milestones

These are the *build-out* milestones of the initial spec, kept for history. The
authoritative phase list for remaining work is the **Coding Agent Upgrade Plan**
at the bottom of this document.

| Milestone | Description | Status |
|---|---|---|
| Phase 1 | Core infra, ingestion, schema | Complete |
| Phase 1.5 | Auth & RBAC | Complete |
| Phase 2 | Embeddings & search | Complete |
| Phase 3 | Event clustering & summaries | Complete |
| Phase 4 | Temporal ranking | Complete |
| Phase 5 | Chat & reports | Complete |
| Phase 6 | Frontend | Complete |
| Phase 7 | Hardening | Complete |

**Overall: original build milestones Phases 1-7 complete. Remaining work is tracked in the upgrade plan below.**

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
- 80% test coverage gate — **386 tests, coverage 80.87%**, verified 2026-10-11 at commit `c3fc039` (`uv run pytest -q --cov=backend`)
- k6 load-test scripts for search and read endpoints

---

## What's left (beyond the original spec)

Tooling for the three label gates below is ready: `uv run python -m eval.label --task queries|judge|events|quality` walks one blinded item at a time (resumable, saved to `eval/human_labels/`), and `--apply` is the only step that writes a label file.

| Task | Priority | Effort |
|---|---|---|
| Yaswanth's 20% review of `eval/queries.jsonl` (12 of 60 queries in `eval/review_sample.txt`) — the query set is not frozen until this lands, so every Phase 2 number is provisional (`uv run python -m eval.label --task queries`, then `--apply`) | High | Low |
| Blinded human labelling of the judge-agreement sample (`eval/judge_agreement.jsonl`, 52 items) → κ ≥ 0.70 gate (`uv run python -m eval.label --task judge`, `--apply`, then `uv run python -m eval.judge_agreement --score`) | High | Low |
| 20% review of the held-out quality and event label sets (agent-drafted, two passes): `--task quality`, `--task events` | Medium | Low |
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

## Coding Agent Upgrade Plan

Authoritative phase list — order matters, do not renumber. (Numbers here refer to
the *upgrade* plan, not the build milestones above.)

| Phase | Description | Status |
|---|---|---|
| 1 | Ground every answer in real text — incl. **1.1** grounding pipeline and **1.2** chunk-filter refit, trafilatura extraction, safe re-ingest CLI, corpus cleanup | **DONE** (1.1 and 1.2 complete; CI green on `c3fc039`) |
| 2 | Evaluation harness (frozen dataset, retrieval/RAG/clustering/content-quality suites, CI regression gate) | **DONE, measured** — four suites + runner + committed baseline (`eval/results/20261013_5aef4d3_baseline.md`) + mocked exact-equality CI gate; numbers and open human gates in `docs/evidence/phase2_eval.md`. Query set still `draft_unreviewed` |
| 3 | NLI verification of claims against retrieved evidence | Pending |
| 4 | Real tool-using agent loop (replaces the fixed planner->retriever->reasoner->synthesizer pipeline) | Pending |
| 5 | Ranking / eval tuning (driven by Phase 2 ablation numbers) | Pending |
| 6 | Multilingual / data breadth | Pending |
| 7 | Product polish + frontend tests | Pending |
| 8 | Deployment, OpenTelemetry, load testing | Pending |
| 9 | README | Pending |

Known limitations that constrain all of the above (evidence agreement is
title-Jaccard only, deep-planner sub-question hallucination, synthesizer
refusals despite evidence, in-sample classifier metrics, 5 summary-only
articles, dependency-audit exceptions, no deployment/OTel) are listed in the
**KNOWN LIMITATIONS** section of `AGENTS.md`.