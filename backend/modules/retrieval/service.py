"""Semantic search + embedding pipeline (Phase 2, spec-complete).

Search surface
--------------
``POST /api/v1/search`` supports the spec §20 contract: ``query``, ``top_k``,
``mode`` (``semantic`` | ``keyword`` | ``hybrid`` — FR-11) and ``filters``
(date range, source, category, country, language, event — FR-12). The BGE-M3
model and Qdrant client load lazily, so importing the API never triggers a
model download; the stack degrades to a clean 503 when unavailable.

Reranking (FR-13)
-----------------
The top-K retrieved candidates (default 50, ``rerank_top_k``) are reranked by
a cross-encoder (BGE-reranker) to the final top-N (default 10, ``rerank_top_n``)
before display. The reranker loads lazily and search degrades gracefully to
retrieval order if it cannot load — a rerank failure never 503s the API.

Embedding pipeline (FR-8..FR-10)
--------------------------------
``embed_article`` chunks a stored article per spec §15 (256-token target,
40-token overlap, <300-token single chunk), embeds it with BGE-M3 — dense +
sparse vectors in one pass (FR-9) — and upserts points carrying the full §11
payload into the sharded ``pulseai_articles`` collection. RQ jobs on the
``embed`` queue run this in the worker process, decoupled from ingestion
(FR-10), and hand off to the Phase 3 fast-path ``cluster`` job. Temporal
ranking (FR-14/15) is Phase 4 per the spec roadmap §32.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    DatetimeRange,
    Distance,
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchValue,
    PointStruct,
    Prefetch,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.core.storage import get_storage
from backend.db.models import Article, ArticleChunk, Source
from backend.modules.retrieval.chunker import chunk_text, estimate_tokens
from backend.modules.retrieval.schemas import SearchFilters, SearchResult

logger = logging.getLogger(__name__)

COLLECTION_NAME = "pulseai_articles"
EMBEDDING_SIZE = settings.embedding_size  # BGE-M3 dense output dimension

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


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
def get_embedder() -> Any:
    """Lazily load BGE-M3 (dense + sparse in one pass; cached per process)."""
    from FlagEmbedding import BGEM3FlagModel  # deferred: heavy import, no download at startup

    logger.info("Loading %s into memory...", settings.embedding_model)
    return BGEM3FlagModel(settings.embedding_model, use_fp16=False)


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Lazily create the Qdrant client (cached)."""
    return QdrantClient(url=settings.qdrant_url)


@lru_cache(maxsize=1)
def get_reranker() -> Any:
    """Lazily load the FR-13 cross-encoder reranker (cached per process).

    Uses sentence-transformers' ``CrossEncoder`` (already a direct dependency)
    rather than FlagEmbedding's ``FlagReranker``: the latter still calls
    ``tokenizer.prepare_for_model``, which transformers 5.x removed.
    """
    from sentence_transformers import CrossEncoder  # deferred: heavy import

    logger.info("Loading reranker %s into memory...", settings.reranker_model)
    return CrossEncoder(settings.reranker_model, max_length=512)


def ensure_collection(client: QdrantClient | None = None) -> None:
    """Create the vector collection if it does not exist yet (dense+sparse, sharded)."""
    qdrant = client or get_qdrant_client()
    collections = qdrant.get_collections().collections
    if not any(c.name == COLLECTION_NAME for c in collections):
        logger.info(
            "Creating Qdrant collection %s (dense %d + sparse, %d shards)",
            COLLECTION_NAME,
            EMBEDDING_SIZE,
            settings.qdrant_shards,
        )
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config={
                DENSE_VECTOR_NAME: VectorParams(size=EMBEDDING_SIZE, distance=Distance.COSINE),
            },
            sparse_vectors_config={SPARSE_VECTOR_NAME: SparseVectorParams()},
            shard_number=settings.qdrant_shards,
        )


# ---------------------------------------------------------------------------
# Encoding helpers (BGE-M3)
# ---------------------------------------------------------------------------


def _encode_batch(model, texts: list[str]) -> tuple[list[list[float]], list[dict[int, float]]]:
    """One-pass BGE-M3 encode → (dense vectors, sparse ``{token_id: weight}``)."""
    output = model.encode(
        texts,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
        batch_size=settings.embedding_batch_size,
    )
    dense = output["dense_vecs"].tolist()
    sparse = [
        {int(token_id): float(weight) for token_id, weight in weights.items()}
        for weights in output["lexical_weights"]
    ]
    return dense, sparse


def _sparse_vector(weights: dict[int, float]) -> SparseVector:
    """Order BGE-M3 lexical weights into a Qdrant SparseVector."""
    items = sorted(weights.items())
    return SparseVector(indices=[i for i, _ in items], values=[v for _, v in items])


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


def _chunk_payload(article: Article, source: Source | None, chunk: ArticleChunk) -> dict:
    """Full spec §11 payload for a chunk point."""
    return {
        "article_id": str(article.id),
        "chunk_id": str(chunk.id),
        "title": article.title,
        "source_id": str(article.source_id),
        "source_name": source.name if source else None,
        "credibility_score": source.credibility_score if source else None,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "category_code": article.category_code,
        "country_code": article.country_code,
        "language_code": article.language_code,
        "event_id": str(article.event_id) if article.event_id else None,
        "chunk_number": chunk.chunk_number,
        "chunk_text": chunk.chunk_text,
    }


def embed_article(
    db: Session,
    article_id,
    *,
    embedder: Any = None,
    qdrant: QdrantClient | None = None,
) -> EmbedOutcome:
    """Chunk + embed one article (BGE-M3 dense+sparse) and upsert into Qdrant.

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
        pieces = chunk_text(
            text,
            target_tokens=settings.chunk_target_tokens,
            overlap_tokens=settings.chunk_overlap_tokens,
            single_chunk_max_tokens=settings.single_chunk_max_tokens,
        )
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
    source = db.get(Source, article.source_id)
    try:
        dense, sparse = _encode_batch(model, [c.chunk_text for c in pending])
        points = [
            PointStruct(
                id=str(chunk.id),
                vector={
                    DENSE_VECTOR_NAME: dense[i],
                    SPARSE_VECTOR_NAME: _sparse_vector(sparse[i]),
                },
                payload=_chunk_payload(article, source, chunk),
            )
            for i, chunk in enumerate(pending)
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
# Search (FR-11, FR-12 — semantic / keyword / hybrid + filters)
# ---------------------------------------------------------------------------


def _query_filter(filters: SearchFilters | None) -> Filter | None:
    """Translate FR-12 search filters into a Qdrant payload filter."""
    if filters is None:
        return None
    must: list[Any] = []
    if filters.source_id:
        must.append(FieldCondition(key="source_id", match=MatchValue(value=str(filters.source_id))))
    if filters.category_code:
        must.append(
            FieldCondition(key="category_code", match=MatchValue(value=filters.category_code))
        )
    if filters.country_code:
        must.append(
            FieldCondition(key="country_code", match=MatchValue(value=filters.country_code))
        )
    if filters.language_code:
        must.append(
            FieldCondition(key="language_code", match=MatchValue(value=filters.language_code))
        )
    if filters.event_id:
        must.append(FieldCondition(key="event_id", match=MatchValue(value=str(filters.event_id))))
    if filters.date_from or filters.date_to:
        bounds: dict[str, datetime] = {}
        if filters.date_from:
            bounds["gte"] = filters.date_from
        if filters.date_to:
            bounds["lte"] = filters.date_to
        must.append(FieldCondition(key="published_at", range=DatetimeRange(**bounds)))
    return Filter(must=must) if must else None


def search(
    query: str,
    limit: int = 10,
    mode: str = "semantic",
    filters: SearchFilters | None = None,
    embedder: Any = None,
    qdrant: QdrantClient | None = None,
    reranker: Any = None,
) -> list[SearchResult]:
    """Retrieve the nearest article vectors for ``query`` (FR-11/FR-12, FR-13).

    Vectors are stored per chunk, so the collection is over-fetched and the
    results deduplicated by article (keeping each article's best score). When
    reranking is enabled, the top-K candidates (``rerank_top_k``, default 50)
    are re-scored by the FR-13 cross-encoder and the top ``limit`` are
    returned; otherwise retrieval order is used. ``mode`` selects the dense
    (``semantic``), sparse (``keyword``), or fused (``hybrid``) query path.

    ``embedder``/``qdrant``/``reranker`` are injectable for tests; defaults
    resolve lazily. A reranker load failure degrades to retrieval order — it
    never 503s the API.
    """
    try:
        model = embedder or get_embedder()
        client = qdrant or get_qdrant_client()
        ensure_collection(client)

        # Rerank needs a real candidate pool (top-K, default 50), not just the
        # requested result count.
        rerank_model = reranker
        if rerank_model is None and settings.rerank_enabled:
            try:
                rerank_model = get_reranker()
            except Exception as exc:  # noqa: BLE001 - degrade gracefully to retrieval order
                logger.warning("reranker unavailable; skipping rerank: %s", exc)
                rerank_model = None

        query_filter = _query_filter(filters)
        fetch_limit = max(limit, settings.rerank_top_k) if rerank_model is not None else limit * 4
        dense, sparse = _encode_batch(model, [query])

        if mode == "keyword":
            response = client.query_points(
                collection_name=COLLECTION_NAME,
                query=_sparse_vector(sparse[0]),
                using=SPARSE_VECTOR_NAME,
                query_filter=query_filter,
                limit=fetch_limit,
            )
        elif mode == "hybrid":
            response = client.query_points(
                collection_name=COLLECTION_NAME,
                prefetch=[
                    Prefetch(
                        query=dense[0],
                        using=DENSE_VECTOR_NAME,
                        filter=query_filter,
                        limit=fetch_limit,
                    ),
                    Prefetch(
                        query=_sparse_vector(sparse[0]),
                        using=SPARSE_VECTOR_NAME,
                        filter=query_filter,
                        limit=fetch_limit,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=fetch_limit,
            )
        else:  # semantic (default)
            response = client.query_points(
                collection_name=COLLECTION_NAME,
                query=dense[0],
                using=DENSE_VECTOR_NAME,
                query_filter=query_filter,
                limit=fetch_limit,
            )
    except SearchUnavailableError:
        raise
    except Exception as exc:  # model download failure, Qdrant down, network error
        logger.warning("semantic search unavailable: %s", exc)
        raise SearchUnavailableError("Semantic search is temporarily unavailable") from exc

    # Build the deduplicated candidate set (one hit per article — the chunk
    # with the best retrieval score) with its payload for reranking.
    candidates: list[tuple[SearchResult, dict[str, Any]]] = []
    seen: set[uuid.UUID] = set()
    for hit in response.points:
        payload: dict[str, Any] = hit.payload or {}
        article_id = payload.get("article_id")
        if article_id is None:
            logger.warning("search hit has no article_id payload; skipping")
            continue
        published_at = payload.get("published_at")
        try:
            published_at = datetime.fromisoformat(published_at) if published_at else None
        except TypeError, ValueError:
            published_at = None
        try:
            result = SearchResult(
                article_id=article_id,
                source_id=payload.get("source_id"),
                title=payload.get("title") or "",
                similarity_score=hit.score,
                published_at=published_at,
                chunk_id=payload.get("chunk_id"),
            )
        except ValidationError:
            # e.g. legacy integer IDs from a previous schema generation that
            # cannot resolve to the UUID-keyed articles table — skip, don't 500.
            logger.warning("search hit has an unresolvable article id (%r); skipping", article_id)
            continue
        if result.article_id in seen:
            continue  # chunk-level dedupe: keep each article's best (first) hit
        seen.add(result.article_id)
        candidates.append((result, payload))
        if rerank_model is None and len(candidates) >= limit:
            break

    # FR-13: cross-encoder rerank of the top-K candidates → final top-N.
    if rerank_model is not None and candidates:
        pairs = [
            [query, payload.get("chunk_text") or payload.get("title") or ""]
            for _result, payload in candidates
        ]
        try:
            scores = list(rerank_model.predict(pairs))
            ranked = sorted(zip(candidates, scores, strict=False), key=lambda t: t[1], reverse=True)
            results: list[SearchResult] = []
            for (result, _payload), score in ranked[:limit]:
                result.similarity_score = float(score)
                results.append(result)
            return results
        except Exception as exc:  # noqa: BLE001 - rerank failure degrades, never 503s
            logger.warning("rerank failed; returning retrieval order: %s", exc)

    return [result for result, _payload in candidates[:limit]]
