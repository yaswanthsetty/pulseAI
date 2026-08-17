"""Unit tests for the retrieval search service (fake model/Qdrant, no downloads)."""

import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from backend.core.config import settings
from backend.modules.retrieval import service
from backend.modules.retrieval.schemas import SearchFilters, SearchResult
from qdrant_client.http.models import Fusion, FusionQuery


class FakeEmbedder:
    """BGE-M3-shaped fake: deterministic dense + sparse outputs."""

    def encode(self, texts, **kwargs):
        dense = np.array([[0.1 + i * 0.001] * service.EMBEDDING_SIZE for i in range(len(texts))])
        sparse = [{581: 0.2, 63773: 0.15} for _ in range(len(texts))]
        return {"dense_vecs": dense, "lexical_weights": sparse}


class _CollectionRef:
    def __init__(self, name):
        self.name = name


class _Collections:
    def __init__(self, names):
        self.collections = [_CollectionRef(n) for n in names]


class _Hit:
    def __init__(self, payload, score):
        self.payload = payload
        self.score = score


class _QueryResponse:
    def __init__(self, points):
        self.points = points


class FakeQdrant:
    def __init__(self, collections=None, points=None, fail_on_query=False):
        self._collections = list(collections or [])
        self._points = list(points or [])
        self.fail_on_query = fail_on_query
        self.created: list[dict] = []
        self.queries: list[dict] = []

    def get_collections(self):
        return _Collections(self._collections)

    def create_collection(self, **kwargs):
        self.created.append(kwargs)

    def query_points(self, **kwargs):
        if self.fail_on_query:
            raise ConnectionError("qdrant unreachable")
        self.queries.append(kwargs)
        return _QueryResponse(self._points)


def _hit(article_id, source_id=None, score=0.9, title="Headline"):
    return _Hit(
        {
            "article_id": article_id,
            "source_id": source_id or str(uuid.uuid4()),
            "title": title,
            "chunk_id": str(uuid.uuid4()),
            "published_at": datetime.now(UTC).isoformat(),
        },
        score,
    )


class TestSearch:
    def test_creates_collection_when_missing(self):
        qdrant = FakeQdrant(collections=[])
        results = service.search("AI funding", embedder=FakeEmbedder(), qdrant=qdrant)

        assert results == []
        assert len(qdrant.created) == 1
        config = qdrant.created[0]
        assert config["collection_name"] == service.COLLECTION_NAME
        assert config["vectors_config"]["dense"].size == service.EMBEDDING_SIZE
        assert "sparse" in config["sparse_vectors_config"]
        assert config["shard_number"] == settings.qdrant_shards

    def test_existing_collection_is_not_recreated(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        service.search("AI funding", embedder=FakeEmbedder(), qdrant=qdrant)

        assert qdrant.created == []

    def test_returns_formatted_hits(self):
        article_id = str(uuid.uuid4())
        source_id = str(uuid.uuid4())
        qdrant = FakeQdrant(
            collections=[service.COLLECTION_NAME],
            points=[
                _hit(article_id, source_id=source_id, score=0.91),
                _hit(str(uuid.uuid4()), score=0.82),
            ],
        )

        results = service.search("AI funding", limit=2, embedder=FakeEmbedder(), qdrant=qdrant)

        assert isinstance(results, list)
        assert all(isinstance(r, SearchResult) for r in results)
        assert results[0].article_id == uuid.UUID(article_id)
        assert results[0].source_id == uuid.UUID(source_id)
        assert results[0].title == "Headline"
        assert results[0].similarity_score == pytest.approx(0.91)
        assert results[0].published_at is not None
        assert results[0].chunk_id is not None
        assert results[1].similarity_score == pytest.approx(0.82)

    def test_semantic_mode_queries_dense_vector(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        service.search("x", mode="semantic", embedder=FakeEmbedder(), qdrant=qdrant)

        assert qdrant.queries[0]["using"] == service.DENSE_VECTOR_NAME
        assert "prefetch" not in qdrant.queries[0]

    def test_keyword_mode_queries_sparse_vector(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        service.search("x", mode="keyword", embedder=FakeEmbedder(), qdrant=qdrant)

        assert qdrant.queries[0]["using"] == service.SPARSE_VECTOR_NAME
        assert qdrant.queries[0]["query"].indices == [581, 63773]

    def test_hybrid_mode_uses_rrf_fusion(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        service.search("x", mode="hybrid", embedder=FakeEmbedder(), qdrant=qdrant)

        assert len(qdrant.queries[0]["prefetch"]) == 2
        fusion = qdrant.queries[0]["query"]
        assert isinstance(fusion, FusionQuery)
        assert fusion.fusion == Fusion.RRF

    def test_filters_build_payload_filter(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME])
        filters = SearchFilters(
            source_id=uuid.uuid4(),
            category_code="technology",
            language_code="en",
            date_from=datetime.now(UTC) - timedelta(days=7),
            date_to=datetime.now(UTC),
        )
        service.search("x", filters=filters, embedder=FakeEmbedder(), qdrant=qdrant)

        query_filter = qdrant.queries[0]["query_filter"]
        assert query_filter is not None
        keys = {cond.key for cond in query_filter.must}
        assert {"source_id", "category_code", "language_code", "published_at"} <= keys

    def test_missing_payload_is_skipped(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME], points=[_Hit(None, 0.5)])

        results = service.search("x", embedder=FakeEmbedder(), qdrant=qdrant)

        assert results == []

    def test_legacy_integer_ids_are_skipped_not_crashed(self):
        # Payloads from a previous schema generation carry int ids that cannot
        # resolve to the UUID-keyed articles table.
        qdrant = FakeQdrant(
            collections=[service.COLLECTION_NAME],
            points=[
                _hit(17, source_id=1, score=0.9),
                _hit(str(uuid.uuid4()), source_id=str(uuid.uuid4()), score=0.8),
            ],
        )

        results = service.search("x", embedder=FakeEmbedder(), qdrant=qdrant)

        assert len(results) == 1
        assert results[0].similarity_score == pytest.approx(0.8)

    def test_missing_title_falls_back_to_empty(self):
        article_id = str(uuid.uuid4())
        qdrant = FakeQdrant(
            collections=[service.COLLECTION_NAME],
            points=[_hit(article_id, source_id=str(uuid.uuid4()), title=None)],
        )

        results = service.search("x", embedder=FakeEmbedder(), qdrant=qdrant)

        assert len(results) == 1
        assert results[0].title == ""

    def test_qdrant_failure_raises_unavailable(self):
        qdrant = FakeQdrant(collections=[service.COLLECTION_NAME], fail_on_query=True)

        with pytest.raises(service.SearchUnavailableError):
            service.search("x", embedder=FakeEmbedder(), qdrant=qdrant)
