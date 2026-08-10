"""API tests for the ported semantic search endpoint (spec §20/§16 surface)."""

import uuid

from backend.modules.retrieval import service
from backend.modules.retrieval.schemas import SearchResult


class TestSearchApi:
    def test_search_returns_results(self, client, monkeypatch):
        article_id = uuid.uuid4()
        source_id = uuid.uuid4()
        fake_results = [
            SearchResult(
                article_id=article_id,
                source_id=source_id,
                title="Breakthrough in fusion energy",
                similarity_score=0.93,
            )
        ]
        monkeypatch.setattr(service, "search", lambda query, limit: fake_results)

        resp = client.post("/api/v1/search", json={"query": "fusion energy", "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["article_id"] == str(article_id)
        assert body[0]["source_id"] == str(source_id)
        assert body[0]["title"] == "Breakthrough in fusion energy"
        assert body[0]["similarity_score"] == 0.93

    def test_search_empty_until_vectors_populated(self, client, monkeypatch):
        monkeypatch.setattr(service, "search", lambda query, limit: [])

        resp = client.post("/api/v1/search", json={"query": "anything"})

        assert resp.status_code == 200
        assert resp.json() == []

    def test_search_unavailable_returns_503_envelope(self, client, monkeypatch):
        def _boom(query, limit):
            raise service.SearchUnavailableError("Semantic search is temporarily unavailable")

        monkeypatch.setattr(service, "search", _boom)

        resp = client.post("/api/v1/search", json={"query": "anything"})

        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert "request_id" in body["error"]

    def test_validation_error(self, client):
        resp = client.post("/api/v1/search", json={"query": ""})

        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
