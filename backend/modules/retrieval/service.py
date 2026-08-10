"""Semantic search service.

This is the early port of the ``POST /api/v1/search`` endpoint (previously
implemented in the monolithic ``main.py``) onto the modular-monolith
architecture. It keeps the original behavior — BGE-small dense vectors and a
Qdrant cosine collection — but lazy-loads the model and client so importing
the API never triggers a model download, and degrades to a clean 503 when the
retrieval stack is unavailable.

Full hybrid retrieval (dense+sparse), cross-encoder reranking, and temporal
ranking land in Phase 4 (FR-11..FR-13); the embedding *pipeline* that populates
vectors is Phase 2 work.
"""

import logging
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, VectorParams
from sentence_transformers import SentenceTransformer

from backend.core.config import settings
from backend.modules.retrieval.schemas import SearchResult

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pulseai_articles"
EMBEDDING_SIZE = 384  # BAAI/bge-small-en-v1.5 output dimension


class SearchUnavailableError(RuntimeError):
    """Raised when the retrieval stack (embedding model or Qdrant) is unavailable."""


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """Lazily load the embedding model (cached; no download at import time)."""
    logger.info("Loading BAAI/bge-small-en-v1.5 into memory...")
    return SentenceTransformer("BAAI/bge-small-en-v1.5")


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Lazily create the Qdrant client (cached)."""
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the vector collection if it does not exist yet."""
    qdrant = client or get_qdrant_client()
    collections = qdrant.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        logger.info("Creating Qdrant collection %s", COLLECTION_NAME)
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=EMBEDDING_SIZE, distance=Distance.COSINE),
        )


def search(
    query: str,
    limit: int = 5,
    embedder: SentenceTransformer | None = None,
    qdrant: QdrantClient | None = None,
) -> list[SearchResult]:
    """Embed the query and return the nearest article vectors from Qdrant.

    ``embedder``/``qdrant`` are injectable for tests; defaults resolve lazily.
    """
    try:
        model = embedder or get_embedder()
        client = qdrant or get_qdrant_client()
        ensure_collection(client)

        query_vector = model.encode(query).tolist()
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=limit,
        )
    except SearchUnavailableError:
        raise
    except Exception as exc:  # model download failure, Qdrant down, network error
        logger.warning("semantic search unavailable: %s", exc)
        raise SearchUnavailableError("Semantic search is temporarily unavailable") from exc

    results: list[SearchResult] = []
    for hit in response.points:
        payload: dict[str, Any] = hit.payload or {}
        article_id = payload.get("article_id")
        if article_id is None:
            logger.warning("search hit has no article_id payload; skipping")
            continue
        try:
            results.append(
                SearchResult(
                    article_id=article_id,
                    source_id=payload.get("source_id"),
                    title=payload.get("title") or "",
                    similarity_score=hit.score,
                )
            )
        except ValidationError:
            # e.g. legacy integer IDs from a previous schema generation that
            # cannot resolve to the UUID-keyed articles table — skip, don't 500.
            logger.warning("search hit has an unresolvable article id (%r); skipping", article_id)
            continue
    return results
