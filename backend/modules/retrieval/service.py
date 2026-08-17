"""Semantic search + embedding pipeline (Phase 2).

Search surface
--------------
``POST /api/v1/search`` (BGE-small dense vectors, Qdrant cosine). The model
and Qdrant client load lazily so importing the API never triggers a model
download; the stack degrades to a clean 503 when unavailable.

Embedding pipeline (FR-8..FR-10)
--------------------------------
``embed_article`` chunks a stored article into sentence-aligned, token-bounded
pieces (``article_chunks``), embeds them with the *same* model the search
endpoint uses (they must match or queries and documents live in different
vector spaces), and upserts chunk points into the ``pulseai_articles``
collection. RQ jobs on the ``embed`` queue run this in the worker process,
decoupled from ingestion (FR-10).

Full hybrid retrieval (dense+sparse), cross-encoder reranking, and temporal
ranking land in Phase 4 (FR-11..FR-13).
"""

import logging
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.storage import get_storage
from backend.db.models import Article, ArticleChunk
from backend.modules.retrieval.chunker import chunk_text, estimate_tokens
from backend.modules.retrieval.schemas import SearchResult

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pulseai_articles"
EMBEDDING_SIZE = settings.embedding_size  # BAAI/bge-small-en-v1.5 output dimension


class SearchUnavailableError(RuntimeError):
    """Raised when the retrieval stack (embedding model or Qdrant) is unavailable."""


class EmbeddingError(RuntimeError):
    """Raised when chunking/embedding an article fails (chunks marked ``failed``)."""


@dataclass
class EmbedOutcome:
    """Result of one article embedding attempt."""

    status: str  # ok | skipped | already_embedded | not_found
    chunk_count: int = 0
    detail: str = ""


@lru_cache(maxsize=1)
def get_embedder() -> SentenceTransformer:
    """Lazily load the embedding model (cached; no download at import time)."""
    logger.info("Loading %s into memory...", settings.embedding_model)
    return SentenceTransformer(settings.embedding_model)


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


# ---------------------------------------------------------------------------
# Embedding pipeline (FR-8..FR-10)
# ---------------------------------------------------------------------------


def _article_text(article: Article) -> str:
    """Resolve the embeddable text for an article (stored body → preview)."""
    content = ""
    if article.content_ref:
        try:
            content = get_storage().get(article.content_ref).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001 - missing/corrupt object must not kill the job
            logger.warning("article %s body unavailable from storage: %s", article.id, exc)
    if not content.strip():
        content = article.content_preview or article.description or ""
    # Title first: chunk 0's vector is dominated by the headline, which
    # materially improves recall for title-matched queries.
    return f"{article.title}\n\n{content}".strip()


def embed_article(
    db: Session,
    article_id,
    *,
    embedder: SentenceTransformer | None = None,
    qdrant: QdrantClient | None = None,
) -> EmbedOutcome:
    """Chunk + embed one article and upsert its vectors into Qdrant.

    Idempotent: re-running on an already-embedded article is a no-op, and a
    re-run after a partial failure re-embeds only the non-embedded chunks.
    ``embedder``/``qdrant`` are injectable for tests; defaults resolve lazily.
    """
    article = db.get(Article, article_id)
    if article is None:
        return EmbedOutcome(status="not_found")
    if article.processed_at is None:
        return EmbedOutcome(status="skipped", detail="article not processed yet")

    chunks = list(
        db.execute(
            select(ArticleChunk)
            .where(ArticleChunk.article_id == article.id)
            .order_by(ArticleChunk.chunk_number)
        ).scalars()
    )
    if chunks and all(c.embedding_status == "embedded" for c in chunks):
        return EmbedOutcome(status="already_embedded", chunk_count=len(chunks))

    if not chunks:
        text = _article_text(article)
        pieces = chunk_text(text, max_tokens=settings.chunk_max_tokens)
        if not pieces:
            return EmbedOutcome(status="skipped", detail="no embeddable content")
        chunks = [
            ArticleChunk(
                article_id=article.id,
                chunk_number=number,
                chunk_text=piece,
                token_count=estimate_tokens(piece),
            )
            for number, piece in enumerate(pieces)
        ]
        db.add_all(chunks)
        db.flush()  # assign chunk ids (used as Qdrant point ids)

    pending = [c for c in chunks if c.embedding_status != "embedded"]
    if not pending:
        db.commit()
        return EmbedOutcome(status="already_embedded", chunk_count=len(chunks))

    model = embedder or get_embedder()
    client = qdrant or get_qdrant_client()
    ensure_collection(client)
    try:
        vectors = model.encode(
            [c.chunk_text for c in pending],
            normalize_embeddings=True,
            batch_size=settings.embedding_batch_size,
        ).tolist()
        points = [
            PointStruct(
                id=str(chunk.id),
                vector=vector,
                payload={
                    "article_id": str(article.id),
                    "source_id": str(article.source_id),
                    "title": article.title,
                    "chunk_number": chunk.chunk_number,
                    "chunk_text": chunk.chunk_text,
                },
            )
            for chunk, vector in zip(pending, vectors, strict=True)
        ]
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    except Exception as exc:  # noqa: BLE001 - model/qdrant failures are surfaced to RQ
        for chunk in pending:
            chunk.embedding_status = "failed"
        db.commit()
        logger.warning("embedding article %s failed: %s", article.id, exc)
        raise EmbeddingError(f"failed to embed article {article.id}") from exc

    for chunk in pending:
        chunk.embedding_status = "embedded"
        chunk.qdrant_point_id = chunk.id
    db.commit()
    return EmbedOutcome(status="ok", chunk_count=len(chunks))


# ---------------------------------------------------------------------------
# Search (FR-11 early dense surface)
# ---------------------------------------------------------------------------


def search(
    query: str,
    limit: int = 5,
    embedder: SentenceTransformer | None = None,
    qdrant: QdrantClient | None = None,
) -> list[SearchResult]:
    """Embed the query and return the nearest article vectors from Qdrant.

    Vectors are stored per chunk, so the collection is over-fetched and the
    results deduplicated by article (keeping each article's best score) to
    present ``limit`` distinct articles.

    ``embedder``/``qdrant`` are injectable for tests; defaults resolve lazily.
    """
    try:
        model = embedder or get_embedder()
        client = qdrant or get_qdrant_client()
        ensure_collection(client)

        query_vector = model.encode(query).tolist()
        # Over-fetch: several chunks of one article may rank highly; dedupe
        # below collapses them to the article's single best hit.
        response = client.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=limit * 4,
        )
    except SearchUnavailableError:
        raise
    except Exception as exc:  # model download failure, Qdrant down, network error
        logger.warning("semantic search unavailable: %s", exc)
        raise SearchUnavailableError("Semantic search is temporarily unavailable") from exc

    results: list[SearchResult] = []
    seen: set[uuid.UUID] = set()
    for hit in response.points:
        payload: dict[str, Any] = hit.payload or {}
        article_id = payload.get("article_id")
        if article_id is None:
            logger.warning("search hit has no article_id payload; skipping")
            continue
        try:
            result = SearchResult(
                article_id=article_id,
                source_id=payload.get("source_id"),
                title=payload.get("title") or "",
                similarity_score=hit.score,
            )
        except ValidationError:
            # e.g. legacy integer IDs from a previous schema generation that
            # cannot resolve to the UUID-keyed articles table — skip, don't 500.
            logger.warning("search hit has an unresolvable article id (%r); skipping", article_id)
            continue
        if result.article_id in seen:
            continue  # chunk-level dedupe: keep each article's best (first) hit
        seen.add(result.article_id)
        results.append(result)
        if len(results) >= limit:
            break
    return results
