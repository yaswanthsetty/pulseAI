"""Aggregate versioned API router (base path ``/api/v1`` per spec §19).

Every ``/api/v1`` request passes the Redis sliding-window rate limiter
(§19/§23); individual routes enforce RBAC via ``require_role`` (§22).
"""

from fastapi import APIRouter, Depends

from backend.modules.auth.ratelimit import rate_limit_dependency
from backend.modules.auth.router import router as auth_router
from backend.modules.events.router import router as events_router
from backend.modules.ingestion.router import router as ingestion_router
from backend.modules.retrieval.router import router as retrieval_router
from backend.modules.agents.router import router as agents_router

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(rate_limit_dependency)])
api_router.include_router(auth_router)
api_router.include_router(ingestion_router)
api_router.include_router(retrieval_router)
api_router.include_router(events_router)
api_router.include_router(agents_router)
