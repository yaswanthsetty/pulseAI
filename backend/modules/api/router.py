"""Aggregate versioned API router (base path ``/api/v1`` per spec §19)."""

from fastapi import APIRouter

from backend.modules.ingestion.router import router as ingestion_router
from backend.modules.retrieval.router import router as retrieval_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ingestion_router)
api_router.include_router(retrieval_router)
