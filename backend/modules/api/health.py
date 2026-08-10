"""Operational health endpoints (spec §25).

``/healthz`` — liveness: the process is up.
``/readyz``  — readiness: Postgres, Qdrant, and Redis are reachable.
``/health``  — compatibility alias for the original skeleton endpoint.
"""

import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from redis import RedisError
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.database import get_db
from backend.core.queue import get_redis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


def _check_postgres(db: Session) -> tuple[bool, str]:
    try:
        db.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - probe endpoint must never crash
        return False, str(exc)


def _check_qdrant() -> tuple[bool, str]:
    try:
        response = httpx.get(f"{settings.qdrant_url}/healthz", timeout=3.0)
        if response.status_code == 200:
            return True, "ok"
        return False, f"unexpected status {response.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _check_redis() -> tuple[bool, str]:
    try:
        get_redis().ping()
        return True, "ok"
    except RedisError as exc:
        return False, str(exc)


@router.get("/healthz")
def liveness():
    """Liveness: the API process is running."""
    return {"status": "alive", "service": settings.app_name}


@router.get("/readyz")
def readiness(db: Session = Depends(get_db)):
    """Readiness: all infrastructure dependencies are reachable."""
    checks = {
        "postgres": _check_postgres(db),
        "qdrant": _check_qdrant(),
        "redis": _check_redis(),
    }
    all_ready = all(ok for ok, _ in checks.values())
    body = {
        "status": "ready" if all_ready else "not_ready",
        "checks": {
            name: {"status": "ok" if ok else "error", "detail": detail}
            for name, (ok, detail) in checks.items()
        },
    }
    if not all_ready:
        raise HTTPException(status_code=503, detail=body)
    return body


@router.get("/health")
def health(db: Session = Depends(get_db)):
    """Backward-compatible alias of the original ``/health`` endpoint."""
    ok, detail = _check_postgres(db)
    if not ok:
        raise HTTPException(status_code=503, detail=f"Database liveness check failed: {detail}")
    return {"status": "healthy", "database": "connected", "engine": "PulseAI Core Ready"}
