"""Phase 1.2 re-ingest safety: embed-then-swap, idempotence, dry-run, fetch failure."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from backend.core import counters
from backend.core.storage import get_storage
from backend.db.models import Article, ArticleChunk, Source
from backend.modules.ingestion.dedupe import url_hash
from backend.modules.ingestion.fetcher import FetchError
from backend.modules.retrieval.chunker import chunk_text, estimate_tokens
from backend.workers import reingest as reingest_mod
from test_embedding_pipeline import FakeEmbedder, FakeQdrant

HTML_FIXTURES = Path(__file__).parent.parent / "fixtures" / "html"


def _serve_tc_sidebar(url, timeout=None):
    return (HTML_FIXTURES / "tc_sidebar.html").read_text(encoding="utf-8")


OLD_BODY = (
    "The old stored body of this article explains a previous regulatory filing in detail "
    "and quotes two officials who described the timeline as aggressive but achievable for "
    "most operators during the transition period that regulators have now published. "
    "A second paragraph adds further context about compliance budgets and testing."
)
JUNK_TAIL = "Newsletters Subscribe for the industry's biggest tech news Related AI " * 30


@pytest.fixture
def make_article(db):
    def _make(**overrides):
        source = Source(
            name=f"Reingest Source {uuid.uuid4().hex[:6]}",
            rss_url="https://fixture.example.com/feed.xml",
            status="active",
            poll_interval_minutes=15,
        )
        db.add(source)
        db.flush()
        slug = uuid.uuid4().hex[:8]
        data = {
            "source_id": source.id,
            "title": "OpenAI introduces Ultrafast, a new mode that makes GPT-5.6 respond faster",
            "description": "Feed summary used when extraction is unusable.",
            "url": f"https://fixture.example.com/articles/{slug}",
            "url_hash": url_hash(f"https://fixture.example.com/articles/{slug}"),
            "published_at": datetime.now(UTC),
            "processed_at": datetime.now(UTC),
        }
        data.update(overrides)
        article = Article(**data)
        db.add(article)
        db.commit()
        db.refresh(article)
        return article

    return _make


def _seed_stored(db, article, text):
    """Persist body + fully-embedded chunk rows (the 'old version')."""
    key = f"articles/{article.id}.txt"
    get_storage().put(key, text.encode("utf-8"))
    article.content_ref = key
    article.content_preview = text[:100]
    pieces = chunk_text(f"{article.title}\n\n{text}")
    old_ids = []
    for i, piece in enumerate(pieces):
        chunk = ArticleChunk(
            article_id=article.id,
            chunk_number=i,
            chunk_text=piece,
            token_count=estimate_tokens(piece),
            embedding_status="embedded",
        )
        db.add(chunk)
        db.flush()
        chunk.qdrant_point_id = chunk.id
        old_ids.append(chunk.id)
    db.commit()
    return old_ids


def _chunk_ids(db, article):
    return {
        c.id for c in db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).all()
    }


class TestReingestSafety:
    def test_embed_then_swap_updates_article(self, db, make_article, monkeypatch):
        counters.reset()
        article = make_article()
        old_ids = _seed_stored(db, article, OLD_BODY)
        monkeypatch.setattr(reingest_mod, "fetch_url", _serve_tc_sidebar)
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        outcome = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "updated"
        new_ids = _chunk_ids(db, article)
        assert new_ids and not (new_ids & set(old_ids))  # old rows swapped out
        # embed-then-swap: upsert happened before any delete
        assert len(qdrant.upserted) == 1
        upserted_ids = {str(p.id) for p in qdrant.upserted[0]["points"]}
        assert upserted_ids == {str(i) for i in new_ids}
        assert qdrant.deleted, "old points must be deleted after the new upsert"
        _, deleted_ids = qdrant.deleted[0]
        assert set(map(str, deleted_ids)) == {str(i) for i in old_ids}
        # stored content + columns updated
        db.refresh(article)
        stored = get_storage().get(article.content_ref).decode()
        assert "speculative decoding" in stored
        assert article.extractor == "trafilatura"
        assert article.extraction_quality == "ok"

    def test_embed_failure_keeps_old_version(self, db, make_article, monkeypatch):
        article = make_article()
        old_ids = set(_seed_stored(db, article, OLD_BODY))
        monkeypatch.setattr(reingest_mod, "fetch_url", _serve_tc_sidebar)
        qdrant = FakeQdrant(collections=["pulseai_articles"], fail_on_upsert=True)

        outcome = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "failed"
        assert _chunk_ids(db, article) == old_ids  # rollback kept old rows
        assert not qdrant.deleted  # nothing deleted
        db.refresh(article)
        stored = get_storage().get(article.content_ref).decode()
        assert stored == OLD_BODY  # storage untouched

    def test_dry_run_writes_nothing(self, db, make_article, monkeypatch):
        counters.reset()
        article = make_article()
        old_ids = set(_seed_stored(db, article, OLD_BODY))
        monkeypatch.setattr(reingest_mod, "fetch_url", _serve_tc_sidebar)
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        outcome = reingest_mod.reingest_article(
            db, article, embedder=FakeEmbedder(), qdrant=qdrant, dry_run=True
        )

        assert outcome.status == "dry_run"
        assert "->" in outcome.detail or "chunks" in outcome.detail
        assert _chunk_ids(db, article) == old_ids
        assert not qdrant.upserted and not qdrant.deleted
        assert counters.get("pulseai_chunks_rejected", reason="boilerplate") == 0
        db.refresh(article)
        assert get_storage().get(article.content_ref).decode() == OLD_BODY

    def test_second_run_is_idempotent(self, db, make_article, monkeypatch):
        article = make_article()
        _seed_stored(db, article, OLD_BODY)
        html = (HTML_FIXTURES / "tc_sidebar.html").read_text(encoding="utf-8")
        monkeypatch.setattr(reingest_mod, "fetch_url", lambda url, timeout=None: html)
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        first = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)
        second = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)

        assert first.status == "updated"
        assert second.status == "unchanged"
        assert len(qdrant.upserted) == 1  # no second upsert

    def test_fetch_failure_keeps_old_text_and_filters_junk(self, db, make_article, monkeypatch):
        counters.reset()
        article = make_article()
        old_ids = _seed_stored(db, article, OLD_BODY + " " + JUNK_TAIL)

        def _boom(url, timeout=None):
            raise FetchError("connection refused")

        monkeypatch.setattr(reingest_mod, "fetch_url", _boom)
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        outcome = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "fetch_failed"
        assert "re-fetch failed" in outcome.detail or "rejected=" in outcome.detail
        # old junk chunk removed, body chunks kept, storage content unchanged
        rows = db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).all()
        assert rows
        from backend.core.content_quality import is_boilerplate

        assert all(not is_boilerplate(c.chunk_text) for c in rows)
        assert counters.get("pulseai_chunks_rejected", reason="boilerplate") >= 1
        db.refresh(article)
        assert get_storage().get(article.content_ref).decode() == OLD_BODY + " " + JUNK_TAIL
        assert set(_chunk_ids(db, article)) != old_ids
        # embed-then-swap ordering preserved on the filter-only path too
        assert qdrant.upserted and qdrant.deleted

    def test_junk_only_vectors_pruned_when_no_prose(self, db, make_article, monkeypatch):
        """Body-less page whose stored chunks are ALL junk converges to summary/low."""
        counters.reset()
        article = make_article()
        old_ids = _seed_stored(db, article, JUNK_TAIL)
        from backend.core.content_quality import is_boilerplate

        rows = db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).all()
        assert rows and all(is_boilerplate(c.chunk_text) for c in rows)
        # re-fetch yields a page with no extractable prose at all
        monkeypatch.setattr(reingest_mod, "fetch_url", lambda url, timeout=None: "")
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        outcome = reingest_mod.reingest_article(db, article, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "pruned"
        assert _chunk_ids(db, article) == set()  # junk rows gone
        assert qdrant.deleted  # stale points removed
        _, deleted_ids = qdrant.deleted[0]
        assert set(map(str, deleted_ids)) == {str(i) for i in old_ids}
        db.refresh(article)
        assert article.extraction_quality == "low"
        assert article.extractor == "summary"
        assert get_storage().get(article.content_ref).decode() == article.description

    def test_junk_only_prune_is_dry_run_safe(self, db, make_article, monkeypatch):
        article = make_article()
        old_ids = set(_seed_stored(db, article, JUNK_TAIL))
        monkeypatch.setattr(reingest_mod, "fetch_url", lambda url, timeout=None: "")
        qdrant = FakeQdrant(collections=["pulseai_articles"])

        outcome = reingest_mod.reingest_article(
            db, article, embedder=FakeEmbedder(), qdrant=qdrant, dry_run=True
        )

        assert outcome.status == "dry_run"
        assert "would prune" in outcome.detail
        assert _chunk_ids(db, article) == old_ids  # nothing deleted
        assert not qdrant.deleted
        db.refresh(article)
        assert article.extraction_quality == "ok"  # columns untouched
