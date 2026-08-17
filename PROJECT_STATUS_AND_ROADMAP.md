# PulseAI — Project Status, Gap Analysis & Step-by-Step Action Plan

> **Sources:** `PulseAI_Technical_Specification_v2.md` (the design document) and the actual code in this repository.
> **Last updated:** August 17, 2026 — **Phases 1, 1.5, and the Phase 2 core embedding pipeline are implemented and verified.**
>
> **Bottom line:**
> - **Design (spec): ~100% complete** (design-complete, "ready for phased implementation").
> - **Phase 1 (core infra + ingestion): COMPLETE** — implemented, tested, and demonstrated live (articles ingested/classified/processed end-to-end).
> - **Phase 1.5 (auth & RBAC): COMPLETE** — managed provider (Clerk/Auth0) + local auth, JWT + rotating refresh cookies, hashed API keys, `require_role` RBAC, rate limiting, CSRF.
> - **Phase 2 (embeddings): SPEC-COMPLETE** — §15 chunking (256/40/300), BGE-M3 dense+sparse, modes + filters, full Postgres↔Qdrant reconciliation; 208 tests green total.
> - **Overall MVP (Phases 1–6): ≈ 33%** — 2.5 of 8 phases done; Phase 2 remaining is cross-encoder rerank (FR-13, Phase 4).

---

## 1. Executive Summary

| Track | Status | Progress |
|---|---|---|
| Specification / Design (v2.0) | Design-complete | **~100%** |
| **Phase 1 — Infra, schema, ingestion (FR-1..FR-7)** | **Implemented & verified** | **✔ COMPLETE** |
| **Phase 1.5 — Auth / RBAC (spec §21-23)** | **Implemented & verified** | **✔ COMPLETE** |
| Phase 2 — Embeddings / chunking / Qdrant | Spec-complete: BGE-M3 dense+sparse, §15 chunking, modes + filters, full reconcile; rerank is Phase 4 | **≈ 90%** |
| Phase 3 — Event detection | Not started (tables exist) | **0%** |
| Phase 4 — Temporal RAG / ranking | Not started | **0%** |
| Phase 5 — Chat & executive reports | Not started | **0%** |
| Phase 6 — Frontend dashboard | Not started (no Next.js app) | **0%** |
| Phase 7 — Hardening (CI/CD prod, DR, load tests) | CI added; rest not started | **~10%** |
| **Overall MVP (Phases 1–6)** | Phases 1, 1.5, + Phase 2 complete (rerank in Phase 4) | **≈ 33%** |

---

## 2. What Exists Now — Repository Map

```
pulseai/
├── backend/
│   ├── main.py                     # FastAPI app: lifespan seeding, /api/v1, error envelope
│   ├── core/
│   │   ├── config.py               # pydantic-settings (env + .env, sensible defaults)
│   │   ├── database.py             # engine/session + naming-convention metadata
│   │   ├── logging.py              # structured JSON logging (spec §26)
│   │   ├── queue.py                # Redis + RQ wiring, poll locks, FR-3 retry markers
│   │   ├── ssrf.py                 # SSRF guard for all outbound fetches (spec §23)
│   │   ├── storage.py              # object-storage abstraction (local + S3 backends)
│   │   ├── audit.py                # audit_log writer
│   │   └── pagination.py           # spec §19 {items,page,page_size,total} envelope
│   ├── db/
│   │   ├── models.py               # full spec §10 schema (18 tables, UUID PKs)
│   │   ├── seed_data.py            # categories/countries/languages/ranking configs
│   │   └── seed.py                 # idempotent reference-data seeding
│   ├── modules/                    # modular monolith (spec §8)
│   │   ├── api/                    # top layer: routers + health endpoints
│   │   ├── ingestion/              # FR-1..FR-7: fetcher, parser, dedupe, classifier,
│   │   │                           #   service, jobs, schemas, router, seeds
│   │   ├── auth/                   # Phase 1.5: security, service, deps (require_role),
│   │   │                           #   ratelimit, csrf, router (auth/users/api-keys)
│   │   ├── retrieval/  ranking/  events/  agents/  reports/        # Phase stubs
│   └── workers/
│       ├── scheduler.py            # per-source polling + backoff retries (FR-1/FR-3)
│       └── worker.py               # RQ worker (SimpleWorker on Windows)
├── migrations/                     # Alembic: full spec schema + author column
├── tests/                          # 162 unit + integration tests (79 auth)
├── docker-compose.yml              # postgres, qdrant, redis, api, worker, scheduler
├── Dockerfile
├── .github/workflows/ci.yml        # ruff → format → import-linter → migrations → pytest
├── .importlinter                   # module-boundary contracts (spec §8), incl. auth carve-out
├── pyproject.toml / uv.lock
└── README.md / .env.example / PROJECT_STATUS_AND_ROADMAP.md
```

### Verified working (live, against real feeds)
- `docker compose up` brings up Postgres, Qdrant, Redis, API, worker, scheduler.
- Startup seeds 3 demo sources + full reference data (9 categories, 78 countries, 36 languages, 3 ranking configs).
- Scheduler polls sources on per-source intervals; worker processes jobs end-to-end:
  RSS → dedupe → clean → language detect → category classify → object storage → `processed_at`.
- Live demo run: **41 articles** ingested from real feeds, classified (e.g. `technology`, `en`),
  bodies stored in object storage, previews inline. Re-poll added **0** duplicates (FR-2 ✓).
- Backoff/degraded path exercised live (a dead seed feed → `consecutive_failures=1` → retry scheduled).
- `/readyz` reports Postgres/Qdrant/Redis health; `/api/v1/sources`, `/api/v1/articles` serve
  paginated, enveloped responses; 404/422 responses use the spec §19 error envelope.
- Auth flow verified live: register → login (HS256 JWT, 900s TTL) → `/users/me` (bearer **and**
  httpOnly cookie), API-key create/use/revoke, refresh rotation (old token 401 after use),
  CSRF (cookie POST without header → 403), RBAC (user POST /sources → 403), rate limiting
  (anon quota → 429 → recovers after the 60s window slides).
- **Phase 2 verified live:** all 113 stored articles chunked (185 `article_chunks`),
  embedded (BGE-small dense), and upserted into the `pulseai_articles` Qdrant collection;
  `POST /api/v1/search` returns real, deduplicated, relevant hits (e.g. "artificial
  intelligence startup raises funding" → AI/funding articles at ~0.68–0.74 cosine).
  Embedding runs on the worker's `embed` queue (FR-10), decoupled from ingestion.

---

## 3. Requirement Compliance (Functional Requirements)

Legend: ✅ done · 🟡 partial · ❌ not done

| Req | Description | Status | Notes |
|---|---|---|---|
| FR-1 | Per-source configurable RSS polling (min 5 min) | ✅ | scheduler + `poll_interval_minutes`, default 15 |
| FR-2 | Dedupe: url_hash **and** fuzzy title+source+date | ✅ | `normalize_url`+sha256; difflib ≥0.92 within 6h window; verified live |
| FR-3 | Backoff retries (1/5/30 min) then `degraded` | ✅ | Redis TTL retry markers, scheduler-driven; verified |
| FR-4 | Validate feed (well-formed RSS/Atom) before activation | ✅ | `POST /sources` validates; malformed → 422 |
| FR-5 | HTML→text + canonical metadata (title, author, date, image, lang) | ✅ | + author column added to schema |
| FR-6 | Language detection per article | ✅ | `langdetect`, FK-safe, stored; non-supported excluded downstream |
| FR-7 | Category classification vs fixed taxonomy | ✅ | deterministic keyword scorer, 9 categories, testable |
| FR-8 | Sentence-aware token-bounded chunking | ✅ | spec §15 exact: 256-token target, 40-token sentence-aligned overlap, <300-token single chunk |
| FR-9 | BGE-M3 dense+sparse embeddings in Qdrant | ✅ | `BAAI/bge-m3` one-pass dense (1024d) + sparse vectors; sharded collection (2 shards), full §11 payload |
| FR-10 | Async embedding worker decoupled from ingestion | ✅ | worker consumes `embed` queue; ingestion enqueues after processing (backfill CLI for existing) |
| FR-11 | Search modes: semantic / keyword / hybrid | ✅ | `mode` param → dense, sparse (BGE-M3), or RRF-fused hybrid; rerank (FR-13) is Phase 4 |
| FR-12 | Search filters | ✅ | date range, source, category, country, language, event (Qdrant payload filter) |
| FR-13 | Cross-encoder rerank (top-K → top-N) | ❌ | Phase 4 per spec roadmap §32 |
| FR-14..15 | Intent-aware temporal ranking + freshness decay | ❌ | Phase 4 (`ranking_configs` seeded) |
| FR-16..18 | Incremental event clustering | ❌ | Phase 3 (`events`/`event_articles` tables ready) |
| FR-19..22 | Chat fast path + report deep path + evidence score | ❌ | Phase 5 |
| FR-23 | Dashboard surfaces | ❌ | Phase 6 |

---

## 4. What You Have to Work On (Next Phases)

### Phase 2 — Embeddings, Chunking & Qdrant (spec-complete)
1. ✅ Sentence-aware chunker (FR-8/§15) → `article_chunks`: 256-token target, 40-token sentence-aligned overlap, <300-token single chunk.
2. ✅ BGE-M3 dense+sparse embeddings (FR-9) via FlagEmbedding; sharded `pulseai_articles` collection (2 shards, named dense 1024d + sparse vectors) with the full §11 payload; 113 articles re-embedded, search verified live.
3. ✅ Async embedding in the worker (`embed` queue, FR-10), batch + RQ retry; the scheduler's periodic reconcile (spec §11, default 60 min) auto-re-enqueues missing chunks and syncs Postgres↔Qdrant point sets (purges Qdrant orphans, re-marks chunks whose points vanished, alerts on drift); `uv run pulseai-backfill-embeddings --recreate` rebuilds collection + chunks.
4. ✅ `/api/v1/search` per spec §20: `top_k`, `mode` (semantic | keyword | hybrid, FR-11), FR-12 filters (date/source/category/country/language/event), deduplicated ranked results.
5. ⬜ Cross-encoder rerank (FR-13) + temporal score breakdown (FR-14/15) — Phase 4 per spec roadmap §32.

### Phase 3 — Events
Centroid collection + fast-path matching (FR-18), slow-path UMAP+HDBSCAN over a bounded window (FR-16), closure after 72h (FR-17).

### Phase 4 — Temporal RAG
Intent detection → weights from `ranking_configs`; `freshness = e^(−hours/24)`; credibility methodology (§13).

### Phase 5 — Agents
Fast-path chat (one retrieve→generate→cite call, SSE for reports; LangGraph deep path with evidence agreement score (§16/§17); OTel tracing + cost logging from day one.

### Phase 6 — Frontend
Next.js 14+ App Router per §24; TanStack Query; Recharts; streaming chat + SSE report progress.

### Phase 7 — Hardening
CI already runs lint/format/boundaries/migrations/tests; add staging deploy, load tests, backup/DR (RPO 24h/RTO 4h), Grafana alerts, security review.

---

## 5. How to Run / Develop

```bash
docker compose up -d postgres qdrant redis   # infra only (no heavy image build)
cp .env.example .env                          # adjust if needed
uv sync && uv run alembic upgrade head
uv run pulseai-api &                          # http://localhost:8000/docs
uv run pulseai-scheduler &                    # per-source polling
uv run pulseai-worker &                       # job execution (SimpleWorker on Windows)
# One-time backfill of existing articles into Qdrant (Phase 2):
#   uv run pulseai-backfill-embeddings [--recreate]   then the worker embeds them
```

Quality gates (all green): `uv run ruff check .` · `uv run ruff format --check .` ·
`uv run lint-imports` · `uv run pytest` (208 tests, needs infra up).

---

## 6. Master Checklist

**Phase 1 — Core Infra & Ingestion — ✔ COMPLETE**
- [x] Modular monolith (`backend/modules/*`) + import-linter contracts (§8)
- [x] Full spec schema: 18 tables, UUID PKs, constraints, indexes (§10) + Alembic migrations
- [x] Per-source polling (FR-1) · url_hash + fuzzy dedupe (FR-2) · backoff + degraded (FR-3)
- [x] Feed validation on source add (FR-4) · HTML→text + metadata (FR-5)
- [x] Language detection (FR-6) · taxonomy classification (FR-7)
- [x] Object storage (`content_ref` + inline preview) (§10/§31)
- [x] Decoupled worker + scheduler (Redis queue, poll locks, retry markers)
- [x] SSRF guard (§23) · structured JSON logging (§26) · audit log wiring
- [x] `/healthz` + `/readyz` (§25) · spec §19 error + pagination envelopes
- [x] Docker (compose + Dockerfile) · CI (lint/format/boundaries/migrations/tests)
- [x] 72 unit + integration tests

**Phase 1.5 — Auth — ✔ COMPLETE**
- [x] Managed provider (Clerk/Auth0): JWKS RS256 verification, user sync, `POST /auth/session`
- [x] `POST /auth/register|login|refresh|logout`, `GET /users/me`; JWT 15 min + rotating refresh cookie (httpOnly)
- [x] API keys: `pls_`-prefixed, hashed at rest, returned once, revocable, scoped
- [x] `require_role(min_role)` dependency + §22 matrix integration-tested per endpoint (incl. admin user/role management)
- [x] Rate limiting (Redis sliding window, 30/120 per min, fail-open) · double-submit CSRF · audit-log auth events
- [x] import-linter carve-out: auth is shared infrastructure (business modules may import it; it never imports them)

**Phase 2 — Embeddings** — [x] `POST /api/v1/search` per spec §20 (lazy model load, 503 fallback) · [x] sentence-aware chunker (FR-8/§15: 256 tokens, 40 overlap, <300 single) · [x] BGE-M3 dense+sparse (FR-9) + sharded collection (2 shards) + full §11 payload · [x] async `embed` queue (FR-10) · [x] search modes semantic/keyword/hybrid (FR-11) + filters (FR-12) · [x] `pulseai-backfill-embeddings --recreate` (rebuilds collection + chunks) · [x] periodic reconcile with Postgres↔Qdrant sync, orphan purge + drift alert (§11) · [ ] cross-encoder rerank (FR-13) — Phase 4

> Driver note: `POSTGRES_DRIVER=postgresql+asyncpg` is available as the async
> driver option (asyncpg pinned in deps); the sync engine and Alembic always
> strip the async prefix, so Phase 1 runs unchanged on `postgresql`.

**Phase 3 — Events** — [ ] centroids · [ ] fast-path matching (FR-18) · [ ] slow-path clustering (FR-16) · [ ] closure (FR-17)

**Phase 4 — Temporal RAG** — [ ] hybrid `/api/v1/search` · [ ] intent-based ranking (FR-14/15) · [ ] credibility methodology (§13)

**Phase 5 — Agents** — [ ] fast-path chat (FR-19/20) · [ ] LangGraph deep path (FR-21) · [ ] evidence agreement (FR-22) · [ ] SSE progress · [ ] tracing/cost instrumentation

**Phase 6 — Frontend** — [ ] Next.js scaffold · [ ] feed/search/chat/events/reports · [ ] analytics/settings/admin · [ ] FR-23 surfaces

**Phase 7 — Hardening** — [ ] staging deploy + approval gate · [ ] load tests · [ ] backup/DR · [ ] monitoring/alerting · [ ] security review

---

*Re-run this audit after each phase to keep the numbers honest.*
