"""Integration tests for the ingestion pipeline (FR-1..FR-7).

Runs against the dedicated ``pulseai_test`` database and local Redis. Outbound
fetches are stubbed with fixture content so tests are hermetic and offline.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backend.core.storage import get_storage
from backend.db.models import Article, Source
from backend.modules.ingestion import service
from backend.modules.ingestion.dedupe import url_hash

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def feed_content() -> str:
    return (FIXTURES / "feed.xml").read_text(encoding="utf-8")


@pytest.fixture
def article_html() -> str:
    return (FIXTURES / "article.html").read_text(encoding="utf-8")


@pytest.fixture
def no_enqueue(monkeypatch):
    """Keep test-enqueued jobs out of Redis (hermetic tests)."""
    monkeypatch.setattr(service.queue, "enqueue_process_article", lambda aid: f"process-{aid}")
    monkeypatch.setattr(service.queue, "enqueue_poll", lambda sid: f"poll-{sid}")


@pytest.fixture
def make_source(db):
    def _make(**overrides):
        data = {
            "name": f"Fixture Feed {uuid.uuid4().hex[:6]}",
            "rss_url": "https://fixture.example.com/feed.xml",
            "website": "https://fixture.example.com",
            "status": "active",
            "poll_interval_minutes": 15,
            "credibility_score": 0.8,
            "credibility_method": "manual",
        }
        data.update(overrides)
        source = Source(**data)
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    return _make


class TestPollSource:
    def test_ingests_new_articles(self, db, make_source, feed_content, no_enqueue, monkeypatch):
        source = make_source()
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)

        outcome = service.poll_source(db, source.id)

        assert outcome.status == "ok"
        # 3 entries in the fixture; the third is a fuzzy duplicate of the first.
        assert outcome.added == 2

        articles = db.query(Article).order_by(Article.published_at).all()
        assert len(articles) == 2
        assert all(a.source_id == source.id for a in articles)

        # URL normalized (tracking params stripped) and hashed.
        first = next(
            a for a in articles if a.title == "AI Startup Raises $200 Million in Series C Round"
        )
        assert first.url == "https://fixture.example.com/articles/ai-startup-series-c"
        assert first.url_hash == url_hash(first.url)

        # Metadata carried through.
        assert first.author == "Jane Reporter"
        assert first.image_url == "https://fixture.example.com/img/ai.jpg"
        assert first.language_code == "en"

    def test_second_poll_adds_nothing(self, db, make_source, feed_content, no_enqueue, monkeypatch):
        source = make_source()
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        service.poll_source(db, source.id)
        outcome = service.poll_source(db, source.id)
        assert outcome.status == "ok"
        assert outcome.added == 0
        assert db.query(Article).count() == 2

    def test_updates_last_polled_at(self, db, make_source, feed_content, no_enqueue, monkeypatch):
        source = make_source()
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        assert source.last_polled_at is None
        service.poll_source(db, source.id)
        db.refresh(source)
        assert source.last_polled_at is not None
        assert source.consecutive_failures == 0

    def test_fetch_failure_schedules_backoff_retry(self, db, make_source, no_enqueue, monkeypatch):
        source = make_source()

        def boom(url, timeout=None):
            raise service.FetchError("connection refused")

        monkeypatch.setattr(service, "fetch_url", boom)
        outcome = service.poll_source(db, source.id)

        assert outcome.status == "retry_scheduled"
        assert outcome.retry_delay_minutes == 1  # first failure -> 1 minute
        db.refresh(source)
        assert source.consecutive_failures == 1
        assert service.queue.has_pending_retry(str(source.id))

    def test_degraded_after_max_failures(self, db, make_source, no_enqueue, monkeypatch):
        source = make_source()

        def boom(url, timeout=None):
            raise service.FetchError("connection refused")

        monkeypatch.setattr(service, "fetch_url", boom)
        for _ in range(4):
            service.poll_source(db, source.id)

        db.refresh(source)
        assert source.status == "degraded"
        assert source.consecutive_failures == 4

    def test_success_clears_failure_state(
        self, db, make_source, feed_content, no_enqueue, monkeypatch
    ):
        source = make_source()

        def boom(url, timeout=None):
            raise service.FetchError("down")

        monkeypatch.setattr(service, "fetch_url", boom)
        service.poll_source(db, source.id)
        assert db.get(Source, source.id).consecutive_failures == 1

        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: feed_content)
        outcome = service.poll_source(db, source.id)

        assert outcome.status == "ok"
        db.refresh(source)
        assert source.consecutive_failures == 0
        assert source.status == "active"
        assert not service.queue.has_pending_retry(str(source.id))


class TestProcessArticle:
    def test_processes_article_end_to_end(
        self, db, make_source, article_html, no_enqueue, monkeypatch
    ):
        source = make_source()
        article = Article(
            source_id=source.id,
            title="AI Startup Raises $200 Million in Series C Round",
            description="Short summary",
            url="https://fixture.example.com/articles/x",
            url_hash=url_hash("https://fixture.example.com/articles/x"),
            published_at=datetime.now(UTC),
        )
        db.add(article)
        db.commit()
        db.refresh(article)

        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: article_html)
        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert content_ref == f"articles/{article.id}.txt"
        assert article.content_ref == content_ref
        assert get_storage().exists(content_ref)
        assert article.processed_at is not None
        assert article.language_code == "en"
        assert article.category_code == "technology"
        # Preview stored inline (FR-5/§31)
        assert article.content_preview and len(article.content_preview) <= 500 + 20

    def test_is_idempotent(self, db, make_source, article_html, no_enqueue, monkeypatch):
        source = make_source()
        article = Article(
            source_id=source.id,
            title="Some Story",
            url="https://fixture.example.com/articles/y",
            url_hash=url_hash("https://fixture.example.com/articles/y"),
            published_at=datetime.now(UTC),
        )
        db.add(article)
        db.commit()

        calls = {"n": 0}

        def fake_fetch(url, timeout=None):
            calls["n"] += 1
            return article_html

        monkeypatch.setattr(service, "fetch_url", fake_fetch)
        first = service.process_article(db, article.id)
        second = service.process_article(db, article.id)

        assert first == second
        assert calls["n"] == 1  # re-run did not re-fetch


class TestSchedulerQueries:
    def test_due_sources_includes_never_polled(self, db, make_source):
        fresh = make_source()
        now = datetime.now(UTC)
        not_due = make_source(name="Not Due", last_polled_at=now - timedelta(minutes=5))
        degraded = make_source(
            name="Degraded",
            status="degraded",
            consecutive_failures=3,
            last_polled_at=now - timedelta(days=2),
        )
        due = {s.id for s in service.list_due_sources(db, now)}
        assert fresh.id in due
        assert not_due.id not in due
        assert degraded.id not in due

    def test_backoff_sources(self, db, make_source):
        waiting = make_source(name="Waiting", consecutive_failures=1)
        degraded = make_source(name="Too Many", consecutive_failures=5, status="degraded")
        backoff = {s.id for s in service.list_backoff_sources(db)}
        assert waiting.id in backoff
        assert degraded.id not in backoff
