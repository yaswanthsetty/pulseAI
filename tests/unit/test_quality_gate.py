"""Phase 1.2 ingest-time quality gate (extraction_quality / extractor / version)."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from backend.core.content_quality import EXTRACTOR_VERSION
from backend.db.models import Article, Source
from backend.modules.ingestion import service
from backend.modules.ingestion.dedupe import url_hash
from backend.modules.ingestion.fetcher import FetchError

HTML_FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


@pytest.fixture
def make_unprocessed(db):
    def _make(**overrides):
        source = Source(
            name=f"Gate Source {uuid.uuid4().hex[:6]}",
            rss_url="https://fixture.example.com/feed.xml",
            status="active",
            poll_interval_minutes=15,
        )
        db.add(source)
        db.flush()
        slug = uuid.uuid4().hex[:8]
        data = {
            "source_id": source.id,
            "title": "Claude to start watermarking AI-generated text",
            "description": "Anthropic said the watermark will be undetectable to average readers.",
            "url": f"https://fixture.example.com/articles/{slug}",
            "url_hash": url_hash(f"https://fixture.example.com/articles/{slug}"),
            "published_at": datetime.now(UTC),
            "processed_at": None,
        }
        data.update(overrides)
        article = Article(**data)
        db.add(article)
        db.commit()
        db.refresh(article)
        return article

    return _make


def _serve(path: Path):
    def _fetch(url, timeout=None):
        return path.read_text(encoding="utf-8")

    return _fetch


class TestQualityGate:
    def test_good_extraction_is_ok_with_trafilatura(self, db, make_unprocessed, monkeypatch):
        article = make_unprocessed()
        monkeypatch.setattr(service, "fetch_url", _serve(HTML_FIXTURES / "tc_sidebar.html"))

        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert content_ref
        assert article.extraction_quality == "ok"
        assert article.extractor == "trafilatura"
        assert article.extractor_version == EXTRACTOR_VERSION
        stored = service.get_storage().get(content_ref).decode()
        assert "speculative decoding" in stored
        assert "Most Popular" not in stored

    def test_short_body_falls_back_to_summary_and_flags_low(
        self, db, make_unprocessed, monkeypatch
    ):
        article = make_unprocessed()
        tiny = "<html><body><article><p>Too short.</p></article></body></html>"
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: tiny)

        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert article.extraction_quality == "low"
        assert article.extractor == "summary"
        assert article.extractor_version == EXTRACTOR_VERSION
        stored = service.get_storage().get(content_ref).decode()
        assert stored == article.description  # feed summary used verbatim

    def test_whole_body_boilerplate_falls_back_to_summary(self, db, make_unprocessed, monkeypatch):
        article = make_unprocessed()
        rail = "Newsletters Subscribe for the industry's biggest tech news Related AI. " * 20
        junk = f"<html><body><article><p>{rail}</p></article></body></html>"
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: junk)

        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert article.extraction_quality == "low"
        assert article.extractor == "summary"
        stored = service.get_storage().get(content_ref).decode()
        assert stored == article.description

    def test_fetch_failure_falls_back_to_summary(self, db, make_unprocessed, monkeypatch):
        article = make_unprocessed()

        def _boom(url, timeout=None):
            raise FetchError("connection refused")

        monkeypatch.setattr(service, "fetch_url", _boom)

        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert article.extraction_quality == "low"
        assert article.extractor == "summary"
        stored = service.get_storage().get(content_ref).decode()
        assert stored == article.description

    def test_short_but_usable_body_not_swapped(self, db, make_unprocessed, monkeypatch):
        # A few bad tail chunks (or a modest body) must never trigger the gate;
        # only <300 chars or an all-boilerplate body may.
        article = make_unprocessed(description="tiny feed blurb")
        paragraphs = [
            "Regulators published the final wording of the transparency rules on Monday, "
            "narrowing the disclosure window from ninety days to thirty for large labs.",
            "Operators complained that the shorter deadline collides with their annual audit "
            "cycle and will force teams to duplicate paperwork in two quarters.",
            "Consumer groups welcomed the change, arguing that faster notices let users "
            "decide whether to trust a chatbot before problems become widespread.",
            "One deputy director noted that enforcement staff would be allocated in waves, "
            "prioritising systems that handle medical or financial questions from the public.",
            "Market analysts expect smaller vendors to consolidate, since compliance tooling "
            "costs more than some startups spend on engineering in an entire quarter.",
            "The final guidance also clarifies that open-weight releases remain in scope when "
            "a company fine-tunes the model and hosts an interface for end users.",
        ]
        body = (
            "<html><body><article>"
            + "".join(f"<p>{para}</p>" for para in paragraphs)
            + "</article></body></html>"
        )
        monkeypatch.setattr(service, "fetch_url", lambda url, timeout=None: body)

        content_ref = service.process_article(db, article.id)

        db.refresh(article)
        assert article.extraction_quality == "ok"
        assert article.extractor in {"trafilatura", "fallback"}
        stored = service.get_storage().get(content_ref).decode()
        assert "transparency rules" in stored
        assert "open-weight releases" in stored
