"""Focused consistency tests from the adversarial review of the events module.

Defect A: ``_event_from_cluster`` commits Postgres before writing the Qdrant
centroid — a Qdrant failure leaves a committed event with no centroid point
(invisible to the fast path, unrecoverable by the slow path).

Defect B: the timeline endpoint reports ``event.article_count`` as
``total_articles`` even when the actual ``event_articles`` rows disagree —
the response should be internally consistent with the days it returns.
"""

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
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


class _BrokenQdrant:
    """Fake Qdrant whose centroid upsert fails (Qdrant down mid-run)."""

    def __init__(self):
        self.created: list[dict] = []
        self.fail_upsert = True

    def get_collections(self):
        return _Collections([events.CENTROIDS_COLLECTION])

    def create_collection(self, **kwargs):
        self.created.append(kwargs)

    def scroll(self, *args, **kwargs):
        # Serve one dense chunk vector per article so article_vector works;
        # the slow path never scrolls centroids in this test.
        return [_Point("chunk", vector={events.DENSE_VECTOR_NAME: [0.5] * 8})], None

    def query_points(self, **kwargs):
        return _QueryResponse([])

    def upsert(self, **kwargs):
        if self.fail_upsert:
            raise ConnectionError("qdrant unreachable")
        self.upserted = kwargs

    def delete(self, *args, **kwargs):
        pass


def _article_with_chunk(db, title):
    source = Source(
        name=f"Consistency Source {uuid.uuid4().hex[:6]}",
        rss_url="https://fixture.example.com/feed.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id,
        title=title,
        description="A body for consistency testing.",
        url=f"https://fixture.example.com/articles/{uuid.uuid4().hex}",
        url_hash=url_hash(f"https://fixture.example.com/articles/{uuid.uuid4().hex}"),
        published_at=datetime.now(UTC),
        processed_at=datetime.now(UTC),
    )
    db.add(article)
    db.flush()
    db.add(
        ArticleChunk(
            article_id=article.id,
            chunk_number=0,
            chunk_text="x",
            token_count=1,
            embedding_status="embedded",
        )
    )
    db.commit()
    db.refresh(article)
    return article


class TestEventClusterQdrantFailure:
    """Defect A: a centroid write failure must not leave a half-created event."""

    def test_qdrant_failure_rolls_back_event(self, db):
        articles = [_article_with_chunk(db, f"Story {i}") for i in range(4)]
        qdrant = _BrokenQdrant()

        def _clusterer(vectors):
            return np.array([0, 0, 0, 0])

        with pytest.raises(ConnectionError):
            events.cluster_unmatched_articles(db, client=qdrant, hours=None, clusterer=_clusterer)

        # Nothing may be committed: no event, no memberships, articles untouched.
        assert db.query(Event).count() == 0
        assert db.query(EventArticle).count() == 0
        assert all(a.event_id is None for a in articles)

    def test_article_vector_tolerates_chunk_without_dense_vector(self, db):
        # article_vector must skip points that carry no dense vector (e.g. a
        # sparse-only point) instead of crashing on them.
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        class _MixedScrollQdrant:
            def scroll(self, collection_name, scroll_filter=None, **kwargs):
                assert scroll_filter == Filter(
                    must=[FieldCondition(key="article_id", match=MatchValue(value="abc"))]
                )
                return [
                    _Point("p1", vector=None),
                    _Point("p2", vector={events.DENSE_VECTOR_NAME: [0.1] * 8}),
                ], None

        vector = events.article_vector(_MixedScrollQdrant(), "abc")
        assert vector is not None
        assert len(vector) == 8


class TestTimelineTotalArticles:
    """Defect B: the timeline total must match the days it actually returns."""

    def _event_with_articles(self, db, count_claimed, count_actual):
        event = Event(
            title="Count mismatch event",
            status="open",
            article_count=count_claimed,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        for i in range(count_actual):
            article = _article_with_chunk(db, f"Day article {i}")
            db.add(EventArticle(event_id=event.id, article_id=article.id))
        db.commit()
        return event

    def test_total_reflects_rows_not_claim(self, client, db):
        # Simulate drift: article_count says 5, but only 2 memberships exist.
        event = self._event_with_articles(db, count_claimed=5, count_actual=2)

        resp = client.get(f"/api/v1/events/{event.id}/timeline")

        assert resp.status_code == 200
        body = resp.json()
        summed = sum(d["article_count"] for d in body["days"])
        assert summed == 2
        # total_articles must be internally consistent with the days returned.
        assert body["total_articles"] == summed
