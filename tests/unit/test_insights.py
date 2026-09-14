"""Unit tests for the insights module (stats, article detail, trends, compare)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from backend.db.models import (
    Article,
    Event,
    EventArticle,
    Source,
)
from backend.modules.insights import service


def _make_source(db, name: str) -> Source:
    source = Source(
        name=name,
        rss_url=f"https://fixture.example.com/{uuid.uuid4().hex[:6]}.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    return source


def _make_article(db, source: Source, title: str, *, age_hours: float = 0) -> Article:
    article = Article(
        source_id=source.id,
        title=title,
        description=f"Body about {title}",
        url=f"https://fixture.example.com/{uuid.uuid4().hex}",
        url_hash=uuid.uuid4().hex,
        published_at=datetime.now(UTC) - timedelta(hours=age_hours),
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )
    db.add(article)
    db.flush()
    return article


@pytest.fixture
def source(db):
    return _make_source(db, f"Insight Source {uuid.uuid4().hex[:6]}")


# ── Stats ───────────────────────────────────────────────────────────────────


class TestStats:
    def test_counts_and_buckets(self, db, source):
        _make_article(db, source, "Stats article one")
        _make_article(db, source, "Stats article two")
        db.commit()

        stats = service.get_stats(db)
        assert stats["articles_total"] >= 2
        assert stats["sources_total"] >= 1
        assert len(stats["articles_per_day"]) == 14
        # Today's bucket includes the two fresh articles.
        today = stats["articles_per_day"][-1]["count"]
        assert today >= 2

    def test_empty_database_is_safe(self, db):
        stats = service.get_stats(db)
        assert stats["articles_total"] >= 0
        assert len(stats["articles_per_day"]) == 14


# ── Article detail ──────────────────────────────────────────────────────────


class TestArticleDetail:
    def test_returns_article_with_source(self, db, source):
        article = _make_article(db, source, "Detail test article")
        db.commit()

        result = service.get_article_detail(db, article.id)
        assert result is not None
        assert result["article"].title == "Detail test article"
        assert result["source_name"] == source.name
        assert result["event"] is None

    def test_unknown_article_returns_none(self, db):
        assert service.get_article_detail(db, uuid.uuid4()) is None

    def test_finds_event_membership(self, db, source):
        article = _make_article(db, source, "Event member article")
        event = Event(
            title="Test event",
            confidence=0.9,
            status="open",
            article_count=1,
        )
        db.add(event)
        db.flush()
        db.add(EventArticle(event_id=event.id, article_id=article.id))
        db.commit()

        result = service.get_article_detail(db, article.id)
        assert result["event"] is not None
        assert result["event"].id == event.id


# ── Trend detection (momentum) ──────────────────────────────────────────────


class TestMomentum:
    def _event_with_articles(self, db, source, recent: int, previous: int):
        event = Event(
            title="Momentum event",
            confidence=0.9,
            status="open",
            article_count=recent + previous,
        )
        db.add(event)
        db.flush()
        now = datetime.now(UTC)
        for i in range(recent):
            article = _make_article(db, source, f"recent {i}")
            db.add(
                EventArticle(
                    event_id=event.id,
                    article_id=article.id,
                    added_at=now - timedelta(hours=i % 12),
                )
            )
        for i in range(previous):
            article = _make_article(db, source, f"previous {i}")
            db.add(
                EventArticle(
                    event_id=event.id,
                    article_id=article.id,
                    added_at=now - timedelta(hours=30 + i),
                )
            )
        db.commit()
        return event

    def test_rising(self, db, source):
        event = self._event_with_articles(db, source, recent=5, previous=1)
        result = service.compute_event_momentum(db, event.id)
        assert result["direction"] == "rising"
        assert result["momentum"] > 0.15

    def test_cooling(self, db, source):
        event = self._event_with_articles(db, source, recent=0, previous=4)
        result = service.compute_event_momentum(db, event.id)
        assert result["direction"] == "cooling"
        assert result["momentum"] == -1.0

    def test_quiet(self, db, source):
        event = self._event_with_articles(db, source, recent=0, previous=0)
        result = service.compute_event_momentum(db, event.id)
        assert result["direction"] == "quiet"

    def test_unknown_event(self, db):
        assert service.compute_event_momentum(db, uuid.uuid4()) is None

    def test_trending_list_sorted_by_momentum(self, db, source):
        rising = self._event_with_articles(db, source, recent=5, previous=1)
        cooling = self._event_with_articles(db, source, recent=0, previous=3)
        items = service.list_trending_events(db, limit=10)
        by_id = {item["event_id"]: item for item in items}
        assert by_id[rising.id]["momentum"] > by_id[cooling.id]["momentum"]


# ── Cross-source comparison ─────────────────────────────────────────────────


class TestCompareSources:
    def test_shared_topic_coverage(self, db):
        src_a = _make_source(db, "Alpha Outlet")
        src_b = _make_source(db, "Beta Outlet")
        _make_article(db, src_a, "AI regulation passes senate")
        _make_article(db, src_a, "AI regulation amendments filed")
        _make_article(db, src_b, "AI regulation passes senate")
        db.commit()

        result = service.compare_sources(db, "AI regulation", "Alpha Outlet", "Beta Outlet")
        assert "error" not in result
        assert result["source_a"]["article_count"] == 2
        assert result["source_b"]["article_count"] == 1
        assert len(result["source_a"]["top_keywords"]) <= 8

    def test_unknown_source_is_error(self, db):
        result = service.compare_sources(db, "AI", "Nope", "AlsoNope")
        assert "error" in result
