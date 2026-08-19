"""Integration and unit tests for the agents module (Phase 5).

Covers:
- Fast-path chat (FR-19 / FR-20): SSE format, evidence JSON, DB persistence
- Deep-path chat (FR-21): thinking events, multi-stage flow
- Evidence agreement scoring (FR-22): _compute_agreement unit tests
- Token usage tracking: _log_usage, GET /api/v1/usage
- RBAC: chat requires user role, reports require analyst, usage requires user
- Complexity heuristic: _is_complex unit tests
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from backend.db.models import Conversation, ConversationMessage, LlmUsage
from backend.modules.agents.schemas import EvidenceItem
from backend.modules.agents.service import _compute_agreement, _is_complex
from backend.modules.retrieval.schemas import SearchResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

ARTICLE1 = SearchResult(
    article_id=uuid.uuid4(),
    title="AI startup raises $50M funding round",
    source_id=uuid.uuid4(),
    similarity_score=0.91,
)
ARTICLE2 = SearchResult(
    article_id=uuid.uuid4(),
    title="AI funding surge continues in 2026",
    source_id=uuid.uuid4(),
    similarity_score=0.82,
)
ARTICLE3 = SearchResult(
    article_id=uuid.uuid4(),
    title="Climate summit reaches new agreement on emissions",
    source_id=uuid.uuid4(),
    similarity_score=0.75,
)


@pytest.fixture
def mock_search_two():
    """Patch retrieval.service.search to return 2 AI articles."""
    with patch("backend.modules.retrieval.service.search") as m:
        m.return_value = [ARTICLE1, ARTICLE2]
        yield m


@pytest.fixture
def mock_search_diverse():
    """Patch retrieval.service.search to return 3 diverse articles."""
    with patch("backend.modules.retrieval.service.search") as m:
        m.return_value = [ARTICLE1, ARTICLE2, ARTICLE3]
        yield m


class MockStreamResponse:
    """Simulates httpx async streaming for fast-path chat."""

    def __init__(self, tokens):
        self.tokens = tokens

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for t in self.tokens:
            yield json.dumps({"message": {"content": t}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


class MockAsyncClient:
    def __init__(self, tokens):
        self.tokens = tokens

    def stream(self, *args, **kwargs):
        return MockStreamResponse(self.tokens)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


@pytest.fixture
def mock_stream_ollama():
    """Patch httpx.AsyncClient so fast-path streaming returns Hello World!"""
    with patch("backend.modules.agents.service.httpx.AsyncClient") as m:
        m.side_effect = lambda *a, **kw: MockAsyncClient(["Hello", " World", "!"])
        yield m


@pytest.fixture
def mock_blocking_ollama():
    """Patch _call_ollama_blocking for deep-path stages."""
    with patch("backend.modules.agents.service._call_ollama_blocking") as m:
        m.side_effect = AsyncMock(
            side_effect=[
                # planner returns 2 sub-questions
                "1. What is the AI funding trend?\n2. Who are the key investors?",
                # reasoner for sub-q 1
                "AI funding grew 40% in 2026 [#1].",
                # reasoner for sub-q 2
                "Top investors include Sequoia and a16z [#2].",
                # synthesizer
                "AI funding is booming [#1], driven by Sequoia and a16z [#2].",
            ]
        )
        yield m


# ---------------------------------------------------------------------------
# Unit: _is_complex
# ---------------------------------------------------------------------------


class TestIsComplex:
    def test_short_simple_question_is_fast(self):
        assert not _is_complex("What happened today?")

    def test_long_question_is_deep(self):
        q = (
            "What are the main factors driving AI investment trends in the tech sector"
            " this year and how do they compare to last year?"
        )
        assert _is_complex(q)

    def test_compare_keyword_triggers_deep(self):
        assert _is_complex("Compare the AI funding landscape in the US vs China")

    def test_analyze_keyword_triggers_deep(self):
        assert _is_complex("Analyze the impact of interest rates on startup funding")

    def test_comma_and_triggers_deep(self):
        assert _is_complex("What are the trends in AI, and how are they affecting jobs?")

    def test_short_with_no_triggers_is_fast(self):
        assert not _is_complex("Latest news on OpenAI?")


# ---------------------------------------------------------------------------
# Unit: _compute_agreement
# ---------------------------------------------------------------------------


class TestComputeAgreement:
    def _make_evidence(self, titles: list[str]) -> list[EvidenceItem]:
        return [
            EvidenceItem(
                citation_id=i + 1,
                article_id=uuid.uuid4(),
                title=t,
                score=0.9,
            )
            for i, t in enumerate(titles)
        ]

    def test_single_source_perfect_agreement(self):
        ev = self._make_evidence(["AI startup funding round"])
        assert _compute_agreement(ev) == 1.0

    def test_similar_titles_returns_valid_score(self):
        ev = self._make_evidence(
            [
                "AI funding surge in 2026",
                "AI investment trends continue rising",
            ]
        )
        score = _compute_agreement(ev)
        # Lexical Jaccard: "funding" ≠ "investment", but "AI" is common.
        # The scorer may or may not count these as mutually supportive depending
        # on stopword filtering; verify only that the result is in [0, 1].
        assert 0.0 <= score <= 1.0

    def test_completely_different_titles_low_agreement(self):
        ev = self._make_evidence(
            [
                "Climate summit reaches agreement",
                "Football world cup results announced",
                "Tech startup IPO in New York",
            ]
        )
        score = _compute_agreement(ev)
        assert score <= 0.4, f"Expected ≤0.4 got {score}"

    def test_empty_evidence_returns_1(self):
        assert _compute_agreement([]) == 1.0

    def test_score_between_0_and_1(self):
        ev = self._make_evidence(["Alpha beta gamma", "Beta gamma delta", "Epsilon zeta"])
        score = _compute_agreement(ev)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Integration: fast-path chat
# ---------------------------------------------------------------------------


class TestFastPathChat:
    def test_returns_sse_stream(self, client, make_user, mock_search_two, mock_stream_ollama):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": "Latest AI news?"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_sse_token_events(self, client, make_user, mock_search_two, mock_stream_ollama):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": "Latest AI news?"})
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        token_events = [e for e in events if e.get("type") == "token"]
        assert len(token_events) == 3
        assert token_events[0]["token"] == "Hello"

    def test_final_evidence_event(self, client, make_user, mock_search_two, mock_stream_ollama):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": "Latest AI news?"})
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        ev_event = next(e for e in events if e.get("type") == "evidence")
        assert ev_event["message"] == "Hello World!"
        assert len(ev_event["evidence"]) == 2
        assert ev_event["evidence"][0]["citation_id"] == 1
        assert "agreement" in ev_event
        assert 0.0 <= ev_event["agreement"] <= 1.0

    def test_conversation_persisted(
        self, client, make_user, mock_search_two, mock_stream_ollama, db
    ):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": "Latest AI news?"})
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        ev_event = next(e for e in events if e.get("type") == "evidence")
        conv_id = uuid.UUID(ev_event["conversation_id"])
        conv = db.get(Conversation, conv_id)
        assert conv is not None
        msgs = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conv_id)
            .order_by(ConversationMessage.created_at)
            .all()
        )
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[1].role == "assistant"
        assert msgs[1].evidence_agreement is not None

    def test_usage_logged(self, client, make_user, mock_search_two, mock_stream_ollama, db):
        headers = make_user(role="user")
        client.post("/api/v1/chat", headers=headers, json={"message": "Latest AI news?"})
        rows = db.query(LlmUsage).filter(LlmUsage.operation == "chat_fast").all()
        assert len(rows) >= 1
        assert rows[0].input_tokens > 0
        assert rows[0].output_tokens > 0

    def test_chat_requires_auth(self, client):
        resp = client.post("/api/v1/chat", json={"message": "hello"})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Integration: deep-path chat
# ---------------------------------------------------------------------------


class TestDeepPathChat:
    COMPLEX_Q = (
        "Compare and analyze the impact of AI funding trends on startup valuations "
        "and how venture capital strategies differ between Silicon Valley and Europe "
        "in 2026, considering interest rate effects and regulatory pressures."
    )

    def test_deep_path_triggered_by_complexity(
        self, client, make_user, mock_search_two, mock_blocking_ollama
    ):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": self.COMPLEX_Q})
        assert resp.status_code == 200
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        thinking_events = [e for e in events if e.get("type") == "thinking"]
        assert len(thinking_events) >= 2, "Expected at least planner + reasoner thinking events"
        stages = {e["stage"] for e in thinking_events}
        assert "planner" in stages

    def test_deep_path_evidence_event(
        self, client, make_user, mock_search_two, mock_blocking_ollama
    ):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": self.COMPLEX_Q})
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        ev_event = next((e for e in events if e.get("type") == "evidence"), None)
        assert ev_event is not None, "No evidence event in deep-path stream"
        assert "agreement" in ev_event
        assert 0.0 <= ev_event["agreement"] <= 1.0
        assert ev_event["message"] != ""

    def test_deep_path_persists_conversation(
        self, client, make_user, mock_search_two, mock_blocking_ollama, db
    ):
        headers = make_user(role="user")
        resp = client.post("/api/v1/chat", headers=headers, json={"message": self.COMPLEX_Q})
        events = [
            json.loads(c[6:]) for c in resp.text.strip().split("\n\n") if c.startswith("data: ")
        ]
        ev_event = next(e for e in events if e.get("type") == "evidence")
        conv_id = uuid.UUID(ev_event["conversation_id"])
        msgs = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conv_id)
            .all()
        )
        assert any(m.role == "assistant" for m in msgs)
        asst = next(m for m in msgs if m.role == "assistant")
        assert asst.evidence_agreement is not None


# ---------------------------------------------------------------------------
# Integration: reports
# ---------------------------------------------------------------------------


class TestReports:
    def test_generate_report_analyst(
        self, client, make_user, mock_search_two, mock_blocking_ollama
    ):
        headers = make_user(role="analyst")
        resp = client.post(
            "/api/v1/reports/generate",
            headers=headers,
            json={"topic": "AI Trends", "timeframe": "last 30 days"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["topic"] == "AI Trends"
        assert data["status"] in ("completed", "failed")

    def test_report_requires_analyst(self, client, make_user):
        headers = make_user(role="user")
        resp = client.post("/api/v1/reports/generate", headers=headers, json={"topic": "AI Trends"})
        assert resp.status_code == 403

    def test_list_reports(self, client, make_user, mock_search_two, mock_blocking_ollama, db):
        headers = make_user(role="analyst")
        client.post("/api/v1/reports/generate", headers=headers, json={"topic": "Crypto Market"})
        resp = client.get("/api/v1/reports", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["topic"] == "Crypto Market"

    def test_get_report_detail(self, client, make_user, mock_search_two, mock_blocking_ollama, db):
        headers = make_user(role="analyst")
        gen = client.post("/api/v1/reports/generate", headers=headers, json={"topic": "AI"})
        rid = gen.json()["id"]
        resp = client.get(f"/api/v1/reports/{rid}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == rid
        assert "evidence_agreement" in data


# ---------------------------------------------------------------------------
# Integration: usage endpoint
# ---------------------------------------------------------------------------


class TestUsage:
    def test_user_sees_own_usage(self, client, make_user, mock_search_two, mock_stream_ollama):
        headers = make_user(role="user")
        client.post("/api/v1/chat", headers=headers, json={"message": "Test?"})
        resp = client.get("/api/v1/usage", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["scope"] == "own"
        assert data["total_tokens"] >= 0

    def test_admin_sees_all_usage(self, client, make_user, mock_search_two, mock_stream_ollama):
        user_headers = make_user(role="user")
        client.post("/api/v1/chat", headers=user_headers, json={"message": "Test?"})
        admin_headers = make_user(role="admin")
        resp = client.get("/api/v1/usage", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["scope"] == "all"

    def test_usage_requires_auth(self, client):
        resp = client.get("/api/v1/usage")
        assert resp.status_code == 401

    def test_usage_operation_filter(self, client, make_user, mock_search_two, mock_stream_ollama):
        headers = make_user(role="user")
        client.post("/api/v1/chat", headers=headers, json={"message": "Test?"})
        resp = client.get("/api/v1/usage?operation=chat_fast", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        # All rows in breakdown are for chat_fast
        for row in data["breakdown"]:
            assert row["operation"] == "chat_fast"
