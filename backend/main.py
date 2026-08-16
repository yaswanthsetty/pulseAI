"""PulseAI FastAPI application entrypoint.

Responsibilities:
* lifespan — seed reference data (and dev sources when enabled);
* routes    — health endpoints + the versioned ``/api/v1`` routers;
* errors    — every response uses the spec §19 error envelope
  ``{"error": {"code", "message", "request_id"}}``.

Run with: ``uv run pulseai-api`` (or ``uv run uvicorn backend.main:app``).
"""

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.core.config import settings
from backend.core.database import SessionLocal
from backend.core.logging import setup_logging
from backend.db.seed import seed_reference_data
from backend.modules.api.health import router as health_router
from backend.modules.api.router import api_router
from backend.modules.auth.csrf import CSRFMiddleware
from backend.modules.ingestion.seeds import seed_default_sources

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[int, str] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _error_code(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, "ERROR")


def _request_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _seed_data() -> None:
    db = SessionLocal()
    try:
        seed_reference_data(db)
        if settings.seed_default_sources:
            seed_default_sources(db)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    await _seed_data()
    logger.info(
        "PulseAI started (environment=%s, storage=%s)",
        settings.environment,
        settings.storage_backend,
    )
    yield
    logger.info("PulseAI shutting down")


app = FastAPI(
    title="PulseAI — Real-Time News Intelligence Engine",
    description=(
        "Modular-monolith backend: ingestion, temporal retrieval, "
        "event intelligence, and tiered AI reasoning (spec v2.0)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Double-submit CSRF protection for cookie-authenticated mutations (§23).
app.add_middleware(CSRFMiddleware)

app.include_router(health_router)
app.include_router(api_router)


# ---------------------------------------------------------------------------
# Error envelope (spec §19)
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": _error_code(exc.status_code),
                "message": str(exc.detail),
                "request_id": _request_id(request),
            }
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "request_id": _request_id(request),
                "details": jsonable_encoder(exc.errors()),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "request_id": _request_id(request),
            }
        },
    )


def run() -> None:
    """Console-script entrypoint (``pulseai-api``)."""
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
