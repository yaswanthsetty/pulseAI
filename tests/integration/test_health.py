"""Health endpoint tests (spec §25)."""


class TestHealth:
    def test_liveness(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_readiness_all_dependencies_up(self, client):
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ready"
        assert body["checks"]["postgres"]["status"] == "ok"
        assert body["checks"]["qdrant"]["status"] == "ok"
        assert body["checks"]["redis"]["status"] == "ok"

    def test_legacy_health_alias(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"
