"""Pytest environment: dedicated test database + shared fixtures.

Environment variables are set *before* any backend import because settings
are captured at import time (pydantic-settings with lru_cache).
"""

import os
import tempfile

# --- Point the backend at the dedicated test database ----------------------
# Forced (not setdefault): tests must never run against a dev/prod database.
os.environ["POSTGRES_DB"] = "pulseai_test"
os.environ.setdefault("POSTGRES_USER", "pulse_admin")
os.environ.setdefault("POSTGRES_PASSWORD", "pulse_password_123")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("POSTGRES_PORT", "5434")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("SEED_DEFAULT_SOURCES", "false")
os.environ.setdefault("STORAGE_BACKEND", "local")
_TEST_STORAGE_DIR = tempfile.mkdtemp(prefix="pulseai-test-storage-")
os.environ["STORAGE_LOCAL_DIR"] = _TEST_STORAGE_DIR
# --- Auth (Phase 1.5) test defaults -----------------------------------------
# Local provider + known secret; rate limiting off globally so the suite is
# deterministic (dedicated rate-limit tests enable it per-test).
os.environ["AUTH_PROVIDER"] = "none"
os.environ["JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["CSRF_ENABLED"] = "true"
# Rerank (FR-13) off by default in tests: unit tests inject a fake reranker,
# and the API integration tests exercise the degraded (retrieval-order) path.
os.environ["RERANK_ENABLED"] = "false"

import psycopg2  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from backend.core.config import settings  # noqa: E402
from backend.core.database import SessionLocal  # noqa: E402
from backend.db.seed import seed_reference_data  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

# Tables that tests may mutate; lookup data (categories/countries/languages/
# ranking_configs) is seeded and preserved.
_MUTABLE_TABLES = (
    "bookmarks",
    "event_articles",
    "article_chunks",
    "articles",
    "sources",
    "events",
    "api_keys",
    "refresh_tokens",
    "saved_reports",
    "saved_searches",
    "notification_rules",
    "audit_log",
    "users",
)


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    """Create (if needed) and migrate the dedicated test database."""
    admin = psycopg2.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname="postgres",
    )
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (settings.postgres_db,))
        if cur.fetchone() is None:
            cur.execute(f'CREATE DATABASE "{settings.postgres_db}"')
    admin.close()

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    db = SessionLocal()
    try:
        seed_reference_data(db)
    finally:
        db.close()
    yield


@pytest.fixture(autouse=True)
def _clean_between_tests(prepared_database):
    """Empty mutable tables before every test (lookup data is preserved)."""
    session = SessionLocal()
    try:
        tables = ", ".join(_MUTABLE_TABLES)
        session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def db(prepared_database):
    """Clean session against the test database, emptied per test."""
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(prepared_database):
    """FastAPI TestClient (lifespan startup runs; dev-source seeding disabled)."""
    from backend.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def make_user(client):
    """Register + login a user (any role) and return bearer-auth headers.

    Roles other than ``user`` are promoted directly in the DB (test setup
    bypasses the admin-only role-change API on purpose).
    """

    def _make(email: str | None = None, role: str = "user", password: str = "Password!123"):
        import uuid as _uuid

        from backend.db.models import User as _User

        email = email or f"user-{_uuid.uuid4().hex[:8]}@example.com"
        reg = client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "display_name": "Test User"},
        )
        assert reg.status_code == 201, reg.text
        if role != "user":
            db = SessionLocal()
            try:
                user = db.query(_User).filter(_User.email == email).one()
                user.role = role
                db.commit()
            finally:
                db.close()
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        token = login.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def csrf_headers(client):
    """Return the current double-submit CSRF header matching the client's cookie."""

    def _headers():
        from backend.core.config import settings as _settings

        value = client.cookies.get(_settings.csrf_cookie_name)
        assert value, "no CSRF cookie present — log in first"
        return {"X-CSRF-Token": value}

    return _headers
