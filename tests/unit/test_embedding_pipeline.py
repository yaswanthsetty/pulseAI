"""Unit tests for the Phase 2 embedding pipeline (fake model/Qdrant, no downloads)."""

import uuid
from datetime import UTC, datetime

import numpy as np
import pytest
from backend.db.models import Article, ArticleChunk, Source
from backend.modules.ingestion.dedupe import url_hash
from backend.modules.retrieval import service
from backend.modules.retrieval.service import EmbeddingError


class FakeEmbedder:
    """BGE-M3-shaped fake: returns dense vectors + sparse lexical weights."""

    def encode(self, texts, **kwargs):
        dense = np.array([[0.1 + i * 0.001] * service.EMBEDDING_SIZE for i in range(len(texts))])
        sparse = [{581: 0.2 + i * 0.001, 63773: 0.15} for i in range(len(texts))]
        return {"dense_vecs": dense, "lexical_weights": sparse}


class _CollectionRef:
    def __init__(self, name):
        self.name = name


class _Collections:
    def __init__(self, names):
        self.collections = [_CollectionRef(n) for n in names]


class FakeQdrant:
    def __init__(self, collections=None, fail_on_upsert=False):
        self._collections = list(collections or [])
        self.fail_on_upsert = fail_on_upsert
        self.upserted: list[dict] = []
        self.created: list[dict] = []

    def get_collections(self):
        return _Collections(self._collections)

    def create_collection(self, **kwargs):
        self.created.append(kwargs)

    def upsert(self, **kwargs):
        if self.fail_on_upsert:
            raise ConnectionError("qdrant unreachable")
        self.upserted.append(kwargs)


@pytest.fixture
def make_article(db):
    def _make(**overrides):
        source = Source(
            name=f"Pipeline Source {uuid.uuid4().hex[:6]}",
            rss_url="https://fixture.example.com/feed.xml",
            status="active",
            poll_interval_minutes=15,
        )
        db.add(source)
        db.flush()
        data = {
            "source_id": source.id,
            "title": "Fusion reactor hits record output",
            "description": (
                "Scientists at the national laboratory reported a breakthrough in "
                "fusion energy research today."
            ),
            "url": "https://fixture.example.com/articles/fusion",
            "url_hash": url_hash("https://fixture.example.com/articles/fusion"),
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


class TestIngestionHandoff:
    """FR-10: processing an article enqueues its embedding via the embed queue."""

    class _DummySession:
        def close(self):
            pass

    def test_process_article_job_enqueues_embed(self, monkeypatch):
        from backend.modules.ingestion import jobs as ingestion_jobs

        enqueued: list[str] = []
        monkeypatch.setattr(
            ingestion_jobs, "process_article", lambda db, aid: f"articles/{aid}.txt"
        )
        monkeypatch.setattr(
            ingestion_jobs, "enqueue_embed_article", lambda aid: enqueued.append(aid)
        )
        monkeypatch.setattr(ingestion_jobs, "SessionLocal", self._DummySession)

        result = ingestion_jobs.process_article_job("abc-123")

        assert result == {"status": "ok", "content_ref": "articles/abc-123.txt"}
        assert enqueued == ["abc-123"]

    def test_process_article_job_skips_embed_without_content(self, monkeypatch):
        from backend.modules.ingestion import jobs as ingestion_jobs

        enqueued: list[str] = []
        monkeypatch.setattr(ingestion_jobs, "process_article", lambda db, aid: "")
        monkeypatch.setattr(
            ingestion_jobs, "enqueue_embed_article", lambda aid: enqueued.append(aid)
        )
        monkeypatch.setattr(ingestion_jobs, "SessionLocal", self._DummySession)

        result = ingestion_jobs.process_article_job("abc-123")

        assert result == {"status": "ok", "content_ref": ""}
        assert enqueued == []


class TestEmbedToClusterHandoff:
    """FR-18 wiring: a successful embed enqueues the Phase 3 cluster job."""

    class _DummySession:
        def close(self):
            pass

    def test_embed_job_enqueues_cluster_on_success(self, monkeypatch):
        from backend.modules.retrieval import jobs as retrieval_jobs
        from backend.modules.retrieval.service import EmbedOutcome

        clustered: list[str] = []
        monkeypatch.setattr(
            retrieval_jobs,
            "embed_article",
            lambda db, aid: EmbedOutcome(status="ok", chunk_count=1),
        )
        monkeypatch.setattr(
            retrieval_jobs, "enqueue_cluster_article", lambda aid: clustered.append(aid)
        )
        monkeypatch.setattr(retrieval_jobs, "SessionLocal", self._DummySession)

        result = retrieval_jobs.embed_article_job("abc-123")

        assert result["status"] == "ok"
        assert clustered == ["abc-123"]

    def test_embed_job_skips_cluster_on_failure(self, monkeypatch):
        from backend.modules.retrieval import jobs as retrieval_jobs
        from backend.modules.retrieval.service import EmbedOutcome

        clustered: list[str] = []
        monkeypatch.setattr(
            retrieval_jobs,
            "embed_article",
            lambda db, aid: EmbedOutcome(status="skipped", detail="no content"),
        )
        monkeypatch.setattr(
            retrieval_jobs, "enqueue_cluster_article", lambda aid: clustered.append(aid)
        )
        monkeypatch.setattr(retrieval_jobs, "SessionLocal", self._DummySession)

        retrieval_jobs.embed_article_job("abc-123")

        assert clustered == []


class TestEmbedArticle:
    def test_chunks_and_embeds_article(self, db, make_article):
        article = make_article()
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])

        outcome = service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "ok"
        assert outcome.chunk_count >= 1
        chunks = (
            db.query(ArticleChunk)
            .filter(ArticleChunk.article_id == article.id)
            .order_by(ArticleChunk.chunk_number)
            .all()
        )
        assert len(chunks) == outcome.chunk_count
        assert all(c.embedding_status == "embedded" for c in chunks)
        assert all(c.qdrant_point_id == c.id for c in chunks)
        assert [c.chunk_number for c in chunks] == list(range(len(chunks)))
        assert all(c.token_count > 0 for c in chunks)

        assert len(qdrant.upserted) == 1
        points = qdrant.upserted[0]["points"]
        assert len(points) == len(chunks)
        payload = points[0].payload
        assert payload["article_id"] == str(article.id)
        assert payload["chunk_id"] == str(chunks[0].id)
        assert payload["source_id"] == str(article.source_id)
        assert payload["title"] == article.title
        assert payload["chunk_number"] == 0
        assert "fusion" in payload["chunk_text"].lower()
        # Full §11 metadata payload.
        assert payload["source_name"].startswith("Pipeline Source")
        assert payload["credibility_score"] == 0.5
        assert payload["published_at"] == article.published_at.isoformat()
        assert payload["category_code"] is None
        assert payload["language_code"] is None
        assert payload["event_id"] is None
        # Point id is the chunk id (FK-by-convention into Qdrant); the vector
        # carries the named dense (BGE-M3, 1024d) + sparse components (FR-9).
        assert points[0].id == str(chunks[0].id)
        assert len(points[0].vector["dense"]) == service.EMBEDDING_SIZE
        assert set(points[0].vector["sparse"].indices) == {581, 63773}

    def test_long_article_produces_numbered_chunks(self, db, make_article):
        body = " ".join(
            f"Paragraph {i} describes yet another aspect of the research findings. "
            for i in range(120)
        )
        article = make_article(description=body)
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])

        outcome = service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.chunk_count > 1
        chunks = (
            db.query(ArticleChunk)
            .filter(ArticleChunk.article_id == article.id)
            .order_by(ArticleChunk.chunk_number)
            .all()
        )
        assert [c.chunk_number for c in chunks] == list(range(len(chunks)))
        assert all(c.embedding_status == "embedded" for c in chunks)

    def test_rerun_is_idempotent(self, db, make_article):
        article = make_article()
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        embedder = FakeEmbedder()

        first = service.embed_article(db, article.id, embedder=embedder, qdrant=qdrant)
        second = service.embed_article(db, article.id, embedder=embedder, qdrant=qdrant)

        assert second.status == "already_embedded"
        assert second.chunk_count == first.chunk_count
        assert len(qdrant.upserted) == 1  # no second upsert

    def test_unprocessed_article_is_skipped(self, db, make_article):
        article = make_article(processed_at=None)
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])

        outcome = service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "skipped"
        assert db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).count() == 0

    def test_article_without_content_is_skipped(self, db, make_article):
        article = make_article(title="", description=None, content_preview=None)
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])

        outcome = service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=qdrant)

        assert outcome.status == "skipped"
        assert outcome.detail == "no embeddable content"

    def test_missing_article(self, db):
        outcome = service.embed_article(
            db, uuid.uuid4(), embedder=FakeEmbedder(), qdrant=FakeQdrant()
        )
        assert outcome.status == "not_found"

    def test_upsert_failure_marks_chunks_failed(self, db, make_article):
        article = make_article()
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME], fail_on_upsert=True)

        with pytest.raises(EmbeddingError):
            service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=qdrant)

        chunks = db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).all()
        assert chunks
        assert all(c.embedding_status == "failed" for c in chunks)

    def test_retry_after_failure_reembeds_failed_chunks(self, db, make_article):
        article = make_article()
        failing = FakeQdrant(collections=[service.COLLECTION_NAME], fail_on_upsert=True)
        with pytest.raises(EmbeddingError):
            service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=failing)

        healthy = FakeQdrant(collections=[service.COLLECTION_NAME])
        outcome = service.embed_article(db, article.id, embedder=FakeEmbedder(), qdrant=healthy)

        assert outcome.status == "ok"
        chunks = db.query(ArticleChunk).filter(ArticleChunk.article_id == article.id).all()
        assert all(c.embedding_status == "embedded" for c in chunks)
        assert len(healthy.upserted) == 1
