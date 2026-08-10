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
