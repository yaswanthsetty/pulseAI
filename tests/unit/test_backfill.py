"""Unit tests for the embeddings backfill (Phase 2 CLI)."""

import types
import uuid
from datetime import UTC, datetime

from backend.db.models import Article, ArticleChunk, Source
from backend.modules.ingestion.dedupe import url_hash
from backend.workers import backfill


def _article(db, *, title="Backfill Story", processed=True, chunks_embedded=0):
    source = Source(
        name=f"Backfill Source {uuid.uuid4().hex[:6]}",
        rss_url="https://fixture.example.com/feed.xml",
        status="active",
        poll_interval_minutes=15,
    )
    db.add(source)
    db.flush()
    article = Article(
        source_id=source.id,
        title=title,
        description="A short article body for embedding.",
        url=f"https://fixture.example.com/articles/{uuid.uuid4().hex}",
        url_hash=url_hash(f"https://fixture.example.com/articles/{uuid.uuid4().hex}"),
        published_at=datetime.now(UTC),
        processed_at=datetime.now(UTC) if processed else None,
    )
    db.add(article)
    db.flush()
    for number in range(chunks_embedded):
        db.add(
            ArticleChunk(
                article_id=article.id,
                chunk_number=number,
                chunk_text=f"Chunk {number} of the article.",
                token_count=6,
                embedding_status="embedded",
                qdrant_point_id=uuid.uuid4(),
            )
        )
    db.commit()
    return article


class TestListArticlesNeedingEmbedding:
    def test_selects_processed_articles_without_embedded_chunks(self, db):
        needs_embedding = _article(db, title="Needs Embedding")
        _article(db, title="Already Embedded", chunks_embedded=2)
        not_processed = _article(db, title="Not Processed", processed=False)

        selected = backfill.list_articles_needing_embedding(db)

        ids = {a.id for a in selected}
        assert needs_embedding.id in ids
        assert not_processed.id not in ids

    def test_includes_articles_with_failed_chunks(self, db):
        article = _article(db, title="Failed Chunks")
        db.add(
            ArticleChunk(
                article_id=article.id,
                chunk_number=0,
                chunk_text="Broken chunk.",
                token_count=3,
                embedding_status="failed",
            )
        )
        db.commit()

        selected = backfill.list_articles_needing_embedding(db)

        assert article.id in {a.id for a in selected}

    def test_includes_articles_with_pending_chunks(self, db):
        # Pending chunks may be orphans of a crashed embed run (rows created but
        # never embedded, no job queued) — re-enqueue so they get embedded.
        article = _article(db, title="Pending Chunks")
        db.add(
            ArticleChunk(
                article_id=article.id,
                chunk_number=0,
                chunk_text="Pending chunk.",
                token_count=3,
                embedding_status="pending",
            )
        )
        db.commit()

        selected = backfill.list_articles_needing_embedding(db)

        assert article.id in {a.id for a in selected}


def _no_sync(db, client):
    return {"orphans": 0, "missing": 0}


class TestReconcile:
    """Periodic reconciliation (spec §11) — the scheduler's embed-queue sweep."""

    def test_skips_when_interval_not_elapsed(self, db, monkeypatch):
        _article(db, title="Needs Embedding")
        enqueued: list[str] = []
        monkeypatch.setattr(backfill, "acquire_embedding_reconcile", lambda seconds: False)
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: enqueued.append(aid))

        result = backfill.reconcile_embeddings()
        assert result["enqueued"] == 0
        assert enqueued == []

    def test_enqueues_when_due(self, db, monkeypatch):
        needs_embedding = _article(db, title="Needs Embedding")
        done = _article(db, title="Done", chunks_embedded=1)
        # Capture ids up front: reconcile_embeddings() closes the session.
        needed_id, done_id = str(needs_embedding.id), str(done.id)

        enqueued: list[str] = []
        monkeypatch.setattr(backfill, "acquire_embedding_reconcile", lambda seconds: True)
        monkeypatch.setattr(backfill, "SessionLocal", lambda: db)
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: enqueued.append(aid))
        monkeypatch.setattr(backfill, "_sync_qdrant_points", _no_sync)

        result = backfill.reconcile_embeddings(interval_minutes=60)
        assert result == {"enqueued": 1, "orphans_purged": 0, "missing_remarked": 0}
        assert enqueued == [needed_id]
        assert done_id not in enqueued

    def test_returns_enqueued_count(self, db, monkeypatch):
        _article(db, title="One")
        _article(db, title="Two")
        _article(db, title="Three", chunks_embedded=1)
        enqueued: list[str] = []
        monkeypatch.setattr(backfill, "acquire_embedding_reconcile", lambda seconds: True)
        monkeypatch.setattr(backfill, "SessionLocal", lambda: db)
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: enqueued.append(aid))
        monkeypatch.setattr(backfill, "_sync_qdrant_points", _no_sync)

        result = backfill.reconcile_embeddings()
        assert result["enqueued"] == 2
        assert len(enqueued) == 2


class TestSyncQdrantPoints:
    """§11: Postgres article_chunks ↔ Qdrant point reconciliation."""

    class _FakeClient:
        def __init__(self, point_ids):
            self.point_ids = list(point_ids)
            self.deleted: list[str] = []

        def scroll(self, **kwargs):
            return ([types.SimpleNamespace(id=i) for i in self.point_ids], None)

        def delete(self, **kwargs):
            self.deleted.extend(kwargs["point_selector"].points)

    def test_purges_orphan_qdrant_points(self, db):
        article = _article(db, title="Sync")
        db.add(
            ArticleChunk(
                article_id=article.id,
                chunk_number=0,
                chunk_text="Chunk.",
                token_count=3,
                embedding_status="embedded",
                qdrant_point_id=uuid.uuid4(),
            )
        )
        db.commit()
        chunk_id = db.query(ArticleChunk).one().id
        orphan_id = str(uuid.uuid4())
        client = self._FakeClient(point_ids=[str(chunk_id), orphan_id])

        result = backfill._sync_qdrant_points(db, client)

        assert result == {"orphans": 1, "missing": 0}
        assert client.deleted == [orphan_id]

    def test_remarks_embedded_chunks_whose_point_vanished(self, db):
        article = _article(db, title="Sync")
        chunk = ArticleChunk(
            article_id=article.id,
            chunk_number=0,
            chunk_text="Chunk.",
            token_count=3,
            embedding_status="embedded",
            qdrant_point_id=uuid.uuid4(),
        )
        db.add(chunk)
        db.commit()
        client = self._FakeClient(point_ids=[])  # point gone from Qdrant

        result = backfill._sync_qdrant_points(db, client)

        assert result == {"orphans": 0, "missing": 1}
        db.refresh(chunk)
        assert chunk.embedding_status == "failed"
        assert chunk.qdrant_point_id is None

    def test_in_sync_is_noop(self, db):
        article = _article(db, title="Sync")
        chunk = ArticleChunk(
            article_id=article.id,
            chunk_number=0,
            chunk_text="Chunk.",
            token_count=3,
            embedding_status="embedded",
            qdrant_point_id=uuid.uuid4(),
        )
        db.add(chunk)
        db.commit()
        client = self._FakeClient(point_ids=[str(chunk.id)])

        result = backfill._sync_qdrant_points(db, client)

        assert result == {"orphans": 0, "missing": 0}
        assert client.deleted == []


class TestAcquireEmbeddingReconcile:
    """The Redis due-marker: exactly one caller per window wins the run."""

    KEY = "reconcile:embeddings"

    def test_marker_gates_runs(self):
        from backend.core.queue import acquire_embedding_reconcile, get_redis

        get_redis().delete(self.KEY)
        try:
            assert acquire_embedding_reconcile(600) is True  # arms the window
            assert acquire_embedding_reconcile(600) is False  # inside the window
            get_redis().delete(self.KEY)
            assert acquire_embedding_reconcile(600) is True  # new window
        finally:
            get_redis().delete(self.KEY)  # never leave the marker armed


class TestMain:
    def test_enqueues_embed_jobs_for_needed_articles(self, db, monkeypatch):
        needs_embedding = _article(db, title="Needs Embedding")
        done = _article(db, title="Done", chunks_embedded=1)
        # Capture ids up front: backfill.main() closes the session it is given.
        needed_id, done_id = str(needs_embedding.id), str(done.id)

        enqueued: list[str] = []
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: enqueued.append(aid))
        monkeypatch.setattr(backfill, "SessionLocal", lambda: db)
        monkeypatch.setattr(backfill.service, "get_qdrant_client", lambda: FakeClient())
        monkeypatch.setattr(backfill.service, "ensure_collection", lambda client: None)

        assert backfill.main([]) == 0

        assert set(enqueued) == {needed_id}
        assert done_id not in enqueued

    def test_recreate_flag_deletes_collection(self, db, monkeypatch):
        calls: list[str] = []

        class _Ref:
            def __init__(self, name):
                self.name = name

        class _Cols:
            collections = [_Ref(backfill.service.COLLECTION_NAME)]

        class Client:
            def get_collections(self):
                return _Cols()

            def delete_collection(self, name):
                calls.append(name)

        monkeypatch.setattr(backfill, "SessionLocal", lambda: db)
        monkeypatch.setattr(backfill.service, "get_qdrant_client", lambda: Client())
        monkeypatch.setattr(backfill.service, "ensure_collection", lambda client: None)
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: None)

        backfill.main(["--recreate"])

        assert calls == [backfill.service.COLLECTION_NAME]

    def test_recreate_skips_delete_when_collection_absent(self, db, monkeypatch):
        calls: list[str] = []

        class _Cols:
            collections = []

        class Client:
            def get_collections(self):
                return _Cols()

            def delete_collection(self, name):
                calls.append(name)

        monkeypatch.setattr(backfill, "SessionLocal", lambda: db)
        monkeypatch.setattr(backfill.service, "get_qdrant_client", lambda: Client())
        monkeypatch.setattr(backfill.service, "ensure_collection", lambda client: None)
        monkeypatch.setattr(backfill, "enqueue_embed_article", lambda aid: None)

        backfill.main(["--recreate"])

        assert calls == []


class FakeClient:
    """Minimal stand-in for the Qdrant client used by the backfill CLI."""

    def delete_collection(self, name):
        pass
