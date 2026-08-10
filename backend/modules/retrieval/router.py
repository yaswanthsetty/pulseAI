"""Semantic search API (early port of the Phase 2 retrieval surface)."""

from fastapi import APIRouter, HTTPException

from backend.modules.retrieval import service
from backend.modules.retrieval.schemas import SearchQuery, SearchResult

router = APIRouter(tags=["retrieval"])


@router.post("/search", response_model=list[SearchResult])
def semantic_search(payload: SearchQuery):
    """Convert a natural-language query into a vector and retrieve nearest neighbors.

    ``def`` (not ``async``) so FastAPI runs the blocking embed/Qdrant work in a
    threadpool. Vectors are populated by the Phase 2 embedding pipeline; until
    then an empty list is returned once the collection exists.
    """
    try:
        return service.search(query=payload.query, limit=payload.limit)
    except service.SearchUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
