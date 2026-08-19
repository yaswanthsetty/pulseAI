"""Semantic search API (early port of the Phase 2 retrieval surface)."""

from fastapi import APIRouter, HTTPException

from backend.modules.retrieval import service
from backend.modules.retrieval.schemas import SearchQuery, SearchResult

router = APIRouter(tags=["retrieval"])


@router.post("/search", response_model=list[SearchResult])
def semantic_search(payload: SearchQuery):
    """Convert a natural-language query into a vector and retrieve nearest neighbors.

    Supports the spec §20 contract: ``top_k`` (``limit`` kept as a deprecated
    alias), ``mode`` (semantic | keyword | hybrid, FR-11) and ``filters``
    (FR-12). ``def`` (not ``async``) so FastAPI runs the blocking
    embed/Qdrant work in a threadpool.
    """
    try:
        return service.search(
            query=payload.query,
            limit=payload.top_k or payload.limit or 10,
            mode=payload.mode,
            intent=payload.intent,
            filters=payload.filters,
        )
    except service.SearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
