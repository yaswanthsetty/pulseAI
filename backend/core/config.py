"""Application configuration loaded from environment variables / .env.

All runtime configuration for PulseAI lives here so that every process
(API, worker, scheduler, migrations, tests) reads from one source of truth.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """PulseAI configuration. Values are read from the environment, then .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -----------------------------------------------------
    app_name: str = "PulseAI"
    environment: str = "development"  # development | staging | production
    debug: bool = False

    # --- PostgreSQL -------------------------------------------------------
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: str = "5432"
    # SQLAlchemy driver: "postgresql" (psycopg2, sync — default) or
    # "postgresql+asyncpg" (async option for Phase 2 async work).
    postgres_driver: str = "postgresql"

    # --- Qdrant ------------------------------------------------------------
    qdrant_url: str = "http://localhost:6333"

    # --- Embeddings (Phase 2) ------------------------------------------------
    # The embedding pipeline and the search endpoint MUST use the same model
    # (query and document vectors must live in one space). BGE-M3 (FR-9)
    # produces dense + sparse vectors in one pass; both are stored in Qdrant.
    embedding_model: str = "BAAI/bge-m3"
    embedding_size: int = 1024  # BGE-M3 dense output dimension
    embedding_batch_size: int = 16  # CPU-friendly batch for the embed worker
    # FR-8 / spec §15 chunking parameters.
    chunk_target_tokens: int = 256  # target tokens per chunk
    chunk_overlap_tokens: int = 40  # sentence-aligned overlap between chunks
    single_chunk_max_tokens: int = 300  # shorter articles stay a single chunk
    qdrant_shards: int = 2  # spec §11: sharding enabled from day one
    # How often the scheduler's periodic reconcile may re-enqueue embed jobs for
    # articles still missing embedded chunks (spec §11 nightly reconciliation).
    embedding_reconcile_interval_minutes: int = 60
    # Reconcile logs an alert when Postgres/Qdrant drift exceeds this many
    # points (orphans either direction) — indicates a sync bug (spec §11).
    reconcile_drift_alert_threshold: int = 10

    # --- Rerank (FR-13, Phase 2 completion) ------------------------------------
    # Cross-encoder that reranks the top-K retrieved candidates to the final
    # top-N before display (spec §6.4 / §16 fast path). Lazy-loaded; search
    # degrades gracefully (no rerank) if the model cannot load.
    reranker_model: str = "BAAI/bge-reranker-base"
    rerank_enabled: bool = True
    rerank_top_k: int = 50  # candidates fetched from retrieval for reranking
    rerank_top_n: int = 10  # final results after the cross-encoder pass

    # --- Events / clustering (Phase 3, spec §14) ---------------------------------
    # Qdrant collections: articles holds chunk vectors (Phase 2); event
    # centroids holds one point per OPEN event (running average of member
    # article vectors) used by the fast-path match (FR-18).
    qdrant_articles_collection: str = "pulseai_articles"
    qdrant_event_centroids_collection: str = "pulseai_event_centroids"
    # Fast path: attach an article to an event when cosine similarity to the
    # event centroid meets this threshold. Spec §14 suggests ≥ 0.82, but on
    # BGE-M3 centroids (means over many articles) that yields only ~40%
    # recall; 0.72 measures ~80% recall with ~1 false positive per 85 on the
    # live corpus. Configurable — tune per deployment.
    event_match_threshold: float = 0.72
    # Slow path (FR-16): bounded recent window of unmatched articles clustered
    # by UMAP+HDBSCAN; run at most once per interval via the scheduler.
    event_slow_path_window_hours: int = 6
    event_slow_path_interval_minutes: int = 30
    event_min_cluster_size: int = 3
    # Closure (spec §14): events with no new article for this long are closed
    # and dropped from the fast-path centroid collection.
    event_close_hours: int = 72
    event_umap_components: int = 5

    # --- LLM Summary (Ollama) & Chat (Phase 5) -----------------------------
    summary_provider: str = "ollama"  # ollama | none
    summary_model: str = "qwen3.5:9b"
    ollama_url: str = "http://localhost:11434"
    summary_max_tokens: int = 300
    summary_timeout_seconds: int = 120

    chat_provider: str = "ollama"
    chat_model: str = "qwen3.5:9b"
    chat_max_tokens: int = 500
    chat_timeout_seconds: int = 120

    # --- Redis --------------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"

    # --- Object storage ------------------------------------------------------
    # backend: "local" (default, filesystem) | "s3" (S3-compatible, e.g. Supabase Storage)
    storage_backend: str = "local"
    storage_local_dir: str = "./storage/objects"
    storage_s3_bucket: str | None = None
    storage_s3_region: str | None = None

    # --- Ingestion ------------------------------------------------------------
    seed_default_sources: bool = True
    scheduler_tick_seconds: int = 30
    min_poll_interval_minutes: int = 5  # FR-1: polite-crawling floor
    default_poll_interval_minutes: int = 15  # FR-1: default per-source interval
    feed_fetch_timeout_seconds: float = 15.0
    article_fetch_timeout_seconds: float = 10.0
    # FR-3: exponential backoff schedule for failed source fetches (minutes)
    retry_backoff_minutes: list[int] = Field(default_factory=lambda: [1, 5, 30])
    # FR-2: fuzzy duplicate detection (title similarity + same source + date window)
    fuzzy_duplicate_threshold: float = 0.92
    fuzzy_duplicate_window_hours: int = 6
    # FR-6: languages eligible for downstream English-only NLP (post-MVP expands)
    supported_languages: list[str] = Field(default_factory=lambda: ["en"])
    http_user_agent: str = "PulseAI/0.1 (+https://github.com/pulseai; news-intelligence-bot)"
    max_article_storage_chars: int = 50_000
    content_preview_chars: int = 500  # FR-5 / §31: preview length kept inline

    # --- Auth (Phase 1.5) ------------------------------------------------------
    # none (local register/login) | clerk | auth0 (managed identity provider, §21)
    auth_provider: str = "none"
    # Local-mode signing key. MUST be overridden with a strong secret in any
    # deployment that exposes register/login (auth_provider=none).
    jwt_secret: str = "dev-only-secret-change-me"
    jwt_issuer: str = "pulseai"
    jwt_audience: str = "pulseai-api"
    jwt_access_ttl_minutes: int = 15
    refresh_ttl_days: int = 30  # rotated on every use; httpOnly cookie (§21)
    # Managed-provider domains (only read when auth_provider=clerk|auth0).
    clerk_domain: str | None = None
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    # Cookies.
    access_cookie_name: str = "pulseai_access"
    refresh_cookie_name: str = "pulseai_refresh"
    csrf_cookie_name: str = "pulseai_csrf"
    cookie_secure: bool = False  # enable behind HTTPS (HSTS territory, §23)
    # Rate limiting (spec §19/§23) — Redis sliding window.
    rate_limit_enabled: bool = True
    rate_limit_anon_per_minute: int = 30
    rate_limit_auth_per_minute: int = 120
    rate_limit_chat_per_minute: int = 10  # chat/reports endpoints (Phase 5)
    # Double-submit CSRF token protection for cookie-authenticated requests (§23).
    csrf_enabled: bool = True

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for PostgreSQL, built from the individual parts."""
        return (
            f"{self.postgres_driver}://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance (import-time safe for tests/CLI)."""
    return Settings()


settings = get_settings()
