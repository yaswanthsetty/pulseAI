"""Unit tests for the Phase 3 events service (fake Qdrant, no model downloads)."""

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from backend.core.config import settings
from backend.db.models import Article, ArticleChunk, Event, EventArticle, Source
from backend.modules.events import service as events
from backend.modules.ingestion.dedupe import url_hash


class _CollectionRef:
    def __init__(self, name):
        self.name = name


class _Collections:
    def __init__(self, names):
        self.collections = [_CollectionRef(n) for n in names]


class _Point:
    def __init__(self, point_id, vector=None, payload=None):
        self.id = point_id
        self.vector = vector
        self.payload = payload


class _Hit:
    def __init__(self, point_id, score, payload=None):
        self.id = point_id
        self.score = score
        self.payload = payload


class _QueryResponse:
    def __init__(self, points):
        self.points = points


class FakeQdrant:
    """Event-service-shaped fake: in-memory points keyed by collection."""

    def __init__(self, collections=None, chunks=None, centroids=None):
        self._collections = list(collections or [events.CENTROIDS_COLLECTION])
        self._chunks: dict[str, list[_Point]] = dict(chunks or {})
        self._centroids: dict[str, _Point] = dict(centroids or {})
        self.created: list[dict] = []
        self.upserted: list[dict] = []
        self.deleted: list[dict] = []
        self.queries: list[dict] = []

    # --- article_chunks (mean-vector source) ---
    def add_chunk(self, article_id, vector):
        self._chunks.setdefault(str(article_id), []).append(
            _Point(str(uuid.uuid4()), vector={events.DENSE_VECTOR_NAME: vector})
        )

    # --- centroids ---
    def add_centroid(self, event_id, vector, title="Event"):
        self._centroids[str(event_id)] = _Point(
            str(event_id),
            vector={events.DENSE_VECTOR_NAME: vector},
            payload={"event_id": str(event_id), "title": title},
        )

    # --- qdrant client API ---
    def get_collections(self):
        return _Collections(self._collections)

    def create_collection(self, **kwargs):
        self.created.append(kwargs)

    def scroll(
        self,
        collection_name,
        scroll_filter=None,
        limit=100,
        with_payload=False,
        with_vectors=False,
        offset=None,
    ):
        if collection_name == events.CENTROIDS_COLLECTION:
            wanted = None
            if scroll_filter is not None:
                for cond in scroll_filter.must:
                    if cond.key == "event_id":
                        wanted = str(cond.match.value)
            points = (
                list(self._centroids.values())
                if wanted is None
                else [p for eid, p in self._centroids.items() if eid == wanted]
            )
        else:
            article_id = None
            if scroll_filter is not None:
                for cond in scroll_filter.must:
                    if cond.key == "article_id":
                        article_id = str(cond.match.value)
            points = list(self._chunks.get(article_id or "", []))
        return points, None

    def query_points(self, **kwargs):
        self.queries.append(kwargs)
        if not self._centroids:
            return _QueryResponse([])
        threshold = kwargs.get("score_threshold") or -1.0
        hits = [
            _Hit(eid, 0.9, payload=p.payload)
            for eid, p in sorted(self._centroids.items())
            if threshold <= 0.9
        ]
        return _QueryResponse(hits[:1])

    def upsert(self, **kwargs):
        self.upserted.append(kwargs)
        for point in kwargs["points"]:
            if kwargs["collection_name"] == events.CENTROIDS_COLLECTION:
                self._centroids[str(point.id)] = point

    def delete(self, collection_name, point_selector):
        self.deleted.append({"collection": collection_name, "points": point_selector.points})


def _make_article(db, **overrides):
    source = Source(
        name=f"Events Source {uuid.uuid4().hex[:6]}",
        rss_url="https://fixture.example.com/feed.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    data = {
        "source_id": source.id,
        "title": "Fusion reactor hits record output",
        "description": "Scientists reported a breakthrough in fusion energy research.",
        "url": f"https://fixture.example.com/articles/{uuid.uuid4().hex}",
        "url_hash": url_hash(f"https://fixture.example.com/articles/{uuid.uuid4().hex}"),
        "published_at": datetime.now(UTC),
        "processed_at": datetime.now(UTC),
    }
    data.update(overrides)
    article = Article(**data)
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


class TestFastPathMatch:
    """FR-18: a new article is matched against open-event centroids."""

    def test_attaches_article_when_centroid_hit(self, db):
        article = _make_article(db)
        existing = Event(title="Fusion news", status="open", article_count=0)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        qdrant = FakeQdrant()
        qdrant.add_chunk(article.id, [0.5] * settings.embedding_size)
        qdrant.add_centroid(existing.id, [0.5] * settings.embedding_size, title="Fusion news")

        event = events.match_article_to_event(db, article.id, client=qdrant, threshold=0.8)

        assert event is not None
        assert event.id == existing.id
        db.refresh(article)
        assert article.event_id == existing.id
        assert event.article_count == 1
        link = (
            db.query(EventArticle)
            .filter(EventArticle.article_id == article.id, EventArticle.event_id == event.id)
            .one()
        )
        assert link.similarity_at_match == pytest.approx(0.9)

    def test_no_match_below_threshold(self, db):
        article = _make_article(db)
        qdrant = FakeQdrant()
        qdrant.add_chunk(article.id, [0.5] * settings.embedding_size)
        # Fake returns 0.9 but threshold 0.95 filters it out.
        event = events.match_article_to_event(db, article.id, client=qdrant, threshold=0.95)

        assert event is None
        db.refresh(article)
        assert article.event_id is None

    def test_article_without_chunks_is_skipped(self, db):
        article = _make_article(db)
        qdrant = FakeQdrant()

        event = events.match_article_to_event(db, article.id, client=qdrant)

        assert event is None

    def test_already_matched_article_is_idempotent(self, db):
        article = _make_article(db)
        existing = Event(title="Existing", status="open", article_count=3)
        db.add(existing)
        db.commit()
        db.refresh(existing)
        article.event_id = existing.id
        db.commit()
        qdrant = FakeQdrant()

        event = events.match_article_to_event(db, article.id, client=qdrant)

        assert event is not None
        assert event.id == existing.id
        assert event.article_count == 3  # untouched: already attached

    def test_missing_article_returns_none(self, db):
        qdrant = FakeQdrant()
        assert events.match_article_to_event(db, uuid.uuid4(), client=qdrant) is None


class TestSlowPathCluster:
    """FR-16: UMAP+HDBSCAN over unmatched articles creates events."""

    def test_creates_events_for_clusters(self, db):
        # Three articles in two well-separated groups; injectable clusterer.
        articles = [
            _make_article(
                db, title=f"AI funding round {i}", description="An AI startup raised money."
            )
            for i in range(3)
        ]
        others = [
            _make_article(
                db, title=f"Sports final {i}", description="The team won the championship."
            )
            for i in range(3)
        ]
        qdrant = FakeQdrant()
        for a in articles:
            qdrant.add_chunk(a.id, [1.0, 0.0])
            db.add(
                ArticleChunk(
                    article_id=a.id,
                    chunk_number=0,
                    chunk_text="x",
                    token_count=1,
                    embedding_status="embedded",
                )
            )
        for a in others:
            qdrant.add_chunk(a.id, [0.0, 1.0])
            db.add(
                ArticleChunk(
                    article_id=a.id,
                    chunk_number=0,
                    chunk_text="y",
                    token_count=1,
                    embedding_status="embedded",
                )
            )
        db.commit()

        def _clusterer(vectors):
            # First 3 rows → label 0, next 3 → label 1 (2-dim fake).
            return np.array([0, 0, 0, 1, 1, 1])

        created = events.cluster_unmatched_articles(
            db, client=qdrant, hours=None, clusterer=_clusterer
        )

        assert len(created) == 2
        assert {e.article_count for e in created} == {3}
        titles = sorted(e.title for e in created)
        assert titles == ["AI funding round 0", "Sports final 0"]
        for e in created:
            db.refresh(e)
            assert e.status == "open"
            assert e.confidence > 0
        db.flush()
        matched = db.query(Article).filter(Article.event_id.is_not(None)).count()
        assert matched == 6
        assert len(qdrant.upserted) == 2  # one centroid point per event

    def test_fewer_than_min_cluster_size_is_noop(self, db):
        articles = [_make_article(db, title=f"Lonely story {i}") for i in range(2)]
        qdrant = FakeQdrant()
        for a in articles:
            qdrant.add_chunk(a.id, [1.0, 0.0])
            db.add(
                ArticleChunk(
                    article_id=a.id,
                    chunk_number=0,
                    chunk_text="x",
                    token_count=1,
                    embedding_status="embedded",
                )
            )
        db.commit()
        created = events.cluster_unmatched_articles(db, client=qdrant, hours=None)

        assert created == []

    def test_noise_label_creates_no_event(self, db):
        articles = [_make_article(db, title=f"Scattered {i}") for i in range(4)]
        qdrant = FakeQdrant()
        for i, a in enumerate(articles):
            qdrant.add_chunk(a.id, [i, -i])
            db.add(
                ArticleChunk(
                    article_id=a.id,
                    chunk_number=0,
                    chunk_text="x",
                    token_count=1,
                    embedding_status="embedded",
                )
            )
        db.commit()

        def _clusterer(vectors):
            return np.array([-1, -1, -1, -1])  # all noise

        created = events.cluster_unmatched_articles(
            db, client=qdrant, hours=None, clusterer=_clusterer
        )

        assert created == []


class TestClosure:
    """FR-17/§14: events idle too long are closed and dropped from centroids."""

    def test_stale_open_event_is_closed(self, db):
        stale = Event(
            title="Old event",
            status="open",
            last_updated=datetime.now(UTC) - timedelta(hours=200),
        )
        db.add(stale)
        db.commit()
        qdrant = FakeQdrant()
        qdrant.add_centroid(stale.id, [0.5] * settings.embedding_size)

        closed = events.close_stale_events(db, client=qdrant, hours=72)

        assert closed == 1
        db.refresh(stale)
        assert stale.status == "closed"
        assert len(qdrant.deleted) == 1
        assert qdrant.deleted[0]["points"] == [str(stale.id)]

    def test_recent_event_is_kept_open(self, db):
        recent = Event(
            title="Fresh event",
            status="open",
            last_updated=datetime.now(UTC) - timedelta(hours=1),
        )
        db.add(recent)
        db.commit()
        qdrant = FakeQdrant()

        closed = events.close_stale_events(db, client=qdrant, hours=72)

        assert closed == 0
        db.refresh(recent)
        assert recent.status == "open"
