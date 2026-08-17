"""Incremental event clustering — Phase 3 (FR-16..FR-18, spec §14).

Two paths detect and grow events without full-corpus reclustering:

* **Fast path (FR-18)** — every newly embedded article is compared against the
  centroid vectors of currently-OPEN events (a small ``pulseai_event_centroids``
  Qdrant collection). Above ``event_match_threshold`` (default 0.82 cosine) the
  article is attached to that event, the centroid is updated as a running
  average, and ``last_updated`` moves. This is how an event's coverage
  accumulates over time instead of fragmenting.

* **Slow path (FR-16)** — scheduled (default every 30 min), UMAP + HDBSCAN run
  over a *bounded recent window* of unmatched articles (those with embedded
  chunks but no event yet) to detect genuinely new events. Each detected
  cluster becomes an ``events`` row with a generated title, extractive summary,
  confidence score (mean member similarity to the centroid), and a centroid
  point for future fast-path matches (FR-17).

* **Closure (FR-17/§14)** — events with no new article for ``event_close_hours``
  (default 72h) are marked ``status='closed'`` and dropped from the centroid
  collection (kept in Postgres/Qdrant for historical query).

Article-level vectors are computed as the mean of the article's chunk dense
vectors already in ``pulseai_articles`` — no extra model call, and the events
module stays independent of the retrieval module (import-linter contract 2).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import Article, ArticleChunk, Event, EventArticle

logger = logging.getLogger(__name__)

ARTICLES_COLLECTION = settings.qdrant_articles_collection
CENTROIDS_COLLECTION = settings.qdrant_event_centroids_collection
DENSE_VECTOR_NAME = "dense"


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Lazily create the Qdrant client (cached; same URL as the retrieval module)."""
    return QdrantClient(url=settings.qdrant_url)


def ensure_centroids_collection(client: QdrantClient | None = None) -> None:
    """Create the event-centroids collection if it does not exist (dense only)."""
    qdrant = client or get_qdrant_client()
    collections = qdrant.get_collections().collections
    if not any(c.name == CENTROIDS_COLLECTION for c in collections):
        logger.info(
            "Creating event centroids collection %s (dense %d, %d shards)",
            CENTROIDS_COLLECTION,
            settings.embedding_size,
            settings.qdrant_shards,
        )
        qdrant.create_collection(
            collection_name=CENTROIDS_COLLECTION,
            vectors_config={
                DENSE_VECTOR_NAME: VectorParams(
                    size=settings.embedding_size, distance=Distance.COSINE
                )
            },
            shard_number=settings.qdrant_shards,
        )


# ---------------------------------------------------------------------------
# Article vectors (mean of the article's chunk dense vectors — no model call)
# ---------------------------------------------------------------------------


def article_vector(client: QdrantClient, article_id) -> list[float] | None:
    """Mean of an article's chunk dense vectors, or None when it has none."""
    vectors: list[list[float]] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=ARTICLES_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="article_id", match=MatchValue(value=str(article_id)))]
            ),
            limit=100,
            with_payload=False,
            with_vectors=True,
            offset=offset,
        )
        for point in points:
            vec = (
                (point.vector or {}).get(DENSE_VECTOR_NAME)
                if isinstance(point.vector, dict)
                else point.vector
            )
            if vec is not None:
                vectors.append(list(vec))
        if not points or offset is None:
            break
    if not vectors:
        return None
    return list(np.mean(np.asarray(vectors, dtype=float), axis=0))


def _upsert_centroid(client: QdrantClient, event: Event, vector: list[float]) -> None:
    """Write (or refresh) the centroid point for an open event."""
    client.upsert(
        collection_name=CENTROIDS_COLLECTION,
        points=[
            PointStruct(
                id=str(event.id),
                vector={DENSE_VECTOR_NAME: vector},
                payload={
                    "event_id": str(event.id),
                    "title": event.title,
                    "article_count": event.article_count,
                    "last_updated": event.last_updated.isoformat(),
                },
            )
        ],
    )


# ---------------------------------------------------------------------------
# Fast path (FR-18): match a new article against open-event centroids
# ---------------------------------------------------------------------------


def _best_centroid_hit(
    client: QdrantClient, vector: list[float], threshold: float
) -> tuple[str | None, float | None]:
    """Top centroid point at/above ``threshold`` → (event_id, score) or (None, None)."""
    response = client.query_points(
        collection_name=CENTROIDS_COLLECTION,
        query=vector,
        using=DENSE_VECTOR_NAME,
        limit=1,
        score_threshold=threshold,
    )
    if not response.points:
        return None, None
    hit = response.points[0]
    payload = hit.payload or {}
    return payload.get("event_id") or str(hit.id), float(hit.score)


def match_article_to_event(
    db: Session,
    article_id,
    *,
    client: QdrantClient | None = None,
    threshold: float | None = None,
) -> Event | None:
    """Fast-path (FR-18): attach ``article_id`` to its best matching open event.

    Idempotent: an article that already belongs to an event is returned as-is.
    When no centroid is close enough, the article stays unmatched for the next
    slow-path pass. Returns the matched event, or None.
    """
    article = db.get(Article, article_id)
    if article is None:
        return None
    if article.event_id is not None:
        return db.get(Event, article.event_id)

    qdrant = client or get_qdrant_client()
    ensure_centroids_collection(qdrant)
    vector = article_vector(qdrant, article.id)
    if vector is None:
        logger.info("article %s has no embedded chunks; skipping fast-path match", article.id)
        return None

    threshold = settings.event_match_threshold if threshold is None else threshold
    event_id, score = _best_centroid_hit(qdrant, vector, threshold)
    if event_id is None:
        return None

    event = db.get(Event, event_id)
    if event is None or event.status != "open":
        return None

    # Grow the centroid as a running average of member article vectors.
    event_point = next(_scroll_centroid_points(qdrant, event_id), None)
    if event_point is not None:
        centroid = (
            list(event_point.vector[DENSE_VECTOR_NAME])
            if isinstance(event_point.vector, dict)
            else list(event_point.vector)
        )
        n = max(event.article_count, 1)
        new_centroid = [
            (old * n + new) / (n + 1) for old, new in zip(centroid, vector, strict=False)
        ]
    else:
        new_centroid = vector

    event.article_count += 1
    event.last_updated = datetime.now(UTC)
    _upsert_centroid(qdrant, event, vector=new_centroid)
    db.add(
        EventArticle(
            event_id=event.id,
            article_id=article.id,
            similarity_at_match=score,
        )
    )
    article.event_id = event.id
    db.commit()
    logger.info(
        "fast-path: article %s → event %s (score=%.3f, now %d articles)",
        article.id,
        event.id,
        score,
        event.article_count,
    )
    return event


def _scroll_centroid_points(client: QdrantClient, event_id):
    """Yield centroid points for an event id (usually exactly one)."""
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=CENTROIDS_COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="event_id", match=MatchValue(value=str(event_id)))]
            ),
            limit=100,
            with_payload=False,
            with_vectors=True,
            offset=offset,
        )
        if points:
            yield from points
        if not points or offset is None:
            break


# ---------------------------------------------------------------------------
# Slow path (FR-16): UMAP + HDBSCAN over a bounded window of unmatched articles
# ---------------------------------------------------------------------------


def _cluster_labels(vectors: np.ndarray) -> np.ndarray:
    """UMAP (5-dim) + HDBSCAN labels for a batch of article vectors.

    Deterministic (fixed random state). Label -1 = noise (no event).
    """
    import hdbscan  # deferred: heavy import, only used by the slow path
    import umap

    if len(vectors) < 2:
        return np.full(len(vectors), -1, dtype=int)
    reduced = umap.UMAP(
        n_components=settings.event_umap_components,
        random_state=42,
        n_neighbors=max(2, min(15, len(vectors) - 1)),
        min_dist=0.0,
        metric="cosine",
    ).fit_transform(vectors)
    labels = hdbscan.HDBSCAN(
        min_cluster_size=settings.event_min_cluster_size,
        metric="euclidean",
    ).fit_predict(reduced)
    return labels.astype(int)


def _event_from_cluster(
    db: Session,
    client: QdrantClient,
    members: list[tuple[Article, list[float]]],
) -> Event | None:
    """Build an events row + centroid point from one detected cluster (FR-17).

    Title and extractive summary come from the most-central member (the one
    closest to the cluster centroid); confidence is the mean member similarity
    to that centroid.
    """
    if len(members) < settings.event_min_cluster_size:
        return None
    vectors = np.asarray([v for _, v in members], dtype=float)
    centroid = list(np.mean(vectors, axis=0))
    # Cosine similarity to the centroid, per member.
    norm = np.linalg.norm(vectors, axis=1)
    cnorm = np.linalg.norm(centroid) or 1.0
    sims = (vectors @ np.asarray(centroid)) / (norm * cnorm + 1e-9)
    central_idx = int(np.argmax(sims))
    central_article = members[central_idx][0]

    event = Event(
        title=central_article.title,
        summary=(central_article.content_preview or central_article.description or "")[:500]
        or None,
        confidence=float(np.clip(np.mean(sims), 0.0, 1.0)),
        status="open",
        article_count=len(members),
        last_updated=datetime.now(UTC),
    )
    db.add(event)
    db.flush()
    # Write the centroid BEFORE committing: if Qdrant fails, the whole
    # transaction rolls back — no half-created event (Postgres row without a
    # centroid point) can survive. The fast path already orders it this way.
    try:
        _upsert_centroid(client, event, centroid)
    except Exception:
        db.rollback()
        raise
    for article, _vec in members:
        db.add(EventArticle(event_id=event.id, article_id=article.id))
        article.event_id = event.id
    db.commit()
    logger.info(
        "slow-path: new event %s (%d articles, confidence=%.3f)",
        event.id,
        event.article_count,
        event.confidence,
    )
    return event


def list_unmatched_articles(
    db: Session,
    *,
    hours: int | None = None,
    client: QdrantClient | None = None,
) -> list[tuple[Article, list[float]]]:
    """Articles with embedded chunks but no event, optionally in a recent window.

    Returns ``(article, vector)`` pairs ordered by published_at.
    """
    statement = (
        select(Article, ArticleChunk)
        .join(ArticleChunk, ArticleChunk.article_id == Article.id)
        .where(
            Article.event_id.is_(None),
            ArticleChunk.embedding_status == "embedded",
        )
        .order_by(Article.published_at)
    )
    if hours is not None:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        statement = statement.where(Article.published_at >= cutoff)
    rows = db.execute(statement).all()

    qdrant = client or get_qdrant_client()
    by_article: dict[uuid.UUID, Article] = {}
    vectors: dict[uuid.UUID, list[float]] = {}
    for article, _chunk in rows:
        by_article.setdefault(article.id, article)
    for article_id in by_article:
        vec = article_vector(qdrant, article_id)
        if vec is not None:
            vectors[article_id] = vec
    return [(by_article[a], vectors[a]) for a in by_article if a in vectors]


def cluster_unmatched_articles(
    db: Session,
    *,
    client: QdrantClient | None = None,
    hours: int | None = None,
    clusterer=None,
) -> list[Event]:
    """Slow path (FR-16): cluster unmatched articles and create new events.

    ``hours`` bounds the window (None = whole corpus — used by the backfill
    CLI); ``clusterer`` is injectable for tests (default: UMAP+HDBSCAN).
    Returns the newly created events (ordered by creation).
    """
    qdrant = client or get_qdrant_client()
    ensure_centroids_collection(qdrant)
    pairs = list_unmatched_articles(db, hours=hours, client=qdrant)
    if len(pairs) < settings.event_min_cluster_size:
        logger.info("slow-path: only %d unmatched articles; nothing to cluster", len(pairs))
        return []

    vectors = np.asarray([v for _, v in pairs], dtype=float)
    labels = (clusterer or _cluster_labels)(vectors)
    created: list[Event] = []
    by_label: dict[int, list[tuple[Article, list[float]]]] = {}
    for (article, vector), label in zip(pairs, labels, strict=False):
        by_label.setdefault(int(label), []).append((article, vector))
    for label, members in sorted(by_label.items()):
        if label < 0:
            continue  # noise
        event = _event_from_cluster(db, qdrant, members)
        if event is not None:
            created.append(event)
    return created


# ---------------------------------------------------------------------------
# Closure (FR-17 / §14): close events with no activity for event_close_hours
# ---------------------------------------------------------------------------


def close_stale_events(
    db: Session, *, client: QdrantClient | None = None, hours: int | None = None
) -> int:
    """Mark events with no new article for ``hours`` as closed + drop centroids.

    Returns the number of events closed.
    """
    qdrant = client or get_qdrant_client()
    hours = settings.event_close_hours if hours is None else hours
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    events = list(
        db.execute(
            select(Event).where(Event.status == "open", Event.last_updated < cutoff)
        ).scalars()
    )
    for event in events:
        event.status = "closed"
        # Drop from the fast-path collection (kept in Postgres for history).
        try:
            qdrant.delete(
                collection_name=CENTROIDS_COLLECTION,
                point_selector=PointIdsList(points=[str(event.id)]),
            )
        except Exception as exc:  # noqa: BLE001 - closure must not fail on a stale point
            logger.warning("could not delete centroid for closed event %s: %s", event.id, exc)
    db.commit()
    if events:
        logger.info("closed %d stale event(s) (no activity for %dh)", len(events), hours)
    return len(events)
