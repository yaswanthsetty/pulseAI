import json
import uuid
from unittest.mock import patch

import pytest
from backend.db.models import Conversation, ConversationMessage, Report
from backend.modules.retrieval.schemas import SearchResult


@pytest.fixture
def mock_search():
    with patch("backend.modules.retrieval.service.search") as m:
        m.return_value = [
            SearchResult(
                article_id=uuid.uuid4(),
                title="Mock Article 1",
                source_id=uuid.uuid4(),
                similarity_score=0.9,
            ),
            SearchResult(
                article_id=uuid.uuid4(),
                title="Mock Article 2",
                source_id=uuid.uuid4(),
                similarity_score=0.8,
            ),
        ]
        yield m


class MockStreamResponse:
    def __init__(self, content_chunks):
        self.content_chunks = content_chunks

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for chunk in self.content_chunks:
            yield json.dumps({"message": {"content": chunk}})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockAsyncClient:
    def __init__(self, content_chunks):
        self.content_chunks = content_chunks

    def stream(self, method, url, **kwargs):
        return MockStreamResponse(self.content_chunks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def mock_httpx_stream():
    with patch("backend.modules.agents.service.httpx.AsyncClient") as m:

        def side_effect(*args, **kwargs):
            return MockAsyncClient(["Hello", " World", "!"])

        m.side_effect = side_effect
        yield m


def test_chat_stream_new_conversation(client, make_user, mock_search, mock_httpx_stream, db):
    user_headers = make_user(role="user")

    response = client.post(
        "/api/v1/chat", headers=user_headers, json={"message": "What is the news?"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # Check SSE chunks
    chunks = response.text.strip().split("\n\n")
    assert len(chunks) == 4

    for _i, chunk in enumerate(chunks[:3]):
        assert chunk.startswith("data: ")
        data = json.loads(chunk[6:])
        assert "token" in data

    # The last chunk should contain the final evidence payload
    last_chunk = chunks[-1]
    assert last_chunk.startswith("data: ")
    final_data = json.loads(last_chunk[6:])
    assert "message" in final_data
    assert final_data["message"] == "Hello World!"
    assert "conversation_id" in final_data
    assert "evidence" in final_data
    assert len(final_data["evidence"]) == 2
    assert final_data["evidence"][0]["title"] == "Mock Article 1"
    assert final_data["evidence"][0]["citation_id"] == 1

    # Check DB
    conv_id = final_data["conversation_id"]
    conv = db.get(Conversation, uuid.UUID(conv_id))
    assert conv is not None
    assert conv.title == "What is the news?"

    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conv.id)
        .order_by(ConversationMessage.created_at)
        .all()
    )
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "What is the news?"
    assert messages[1].role == "assistant"
    assert messages[1].content == "Hello World!"
    assert "items" in messages[1].evidence
    assert len(messages[1].evidence["items"]) == 2


def test_chat_requires_user_role(client):
    response = client.post("/api/v1/chat", json={"message": "What is the news?"})
    assert response.status_code == 401


def test_generate_report_stub(client, make_user, db):
    analyst_headers = make_user(role="analyst")

    response = client.post(
        "/api/v1/reports/generate",
        headers=analyst_headers,
        json={"topic": "AI Trends", "timeframe": "last 30 days"},
    )

    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["topic"] == "AI Trends"
    assert data["status"] == "completed"

    report_id = data["id"]
    report = db.get(Report, uuid.UUID(report_id))
    assert report is not None
    assert report.topic == "AI Trends"
    assert report.status == "completed"


def test_list_reports(client, make_user, db):
    analyst_headers = make_user(role="analyst")

    # Generate one
    client.post("/api/v1/reports/generate", headers=analyst_headers, json={"topic": "AI Trends"})

    response = client.get("/api/v1/reports", headers=analyst_headers)
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["topic"] == "AI Trends"


def test_reports_require_analyst_role(client, make_user):
    user_headers = make_user(role="user")
    response = client.post(
        "/api/v1/reports/generate", headers=user_headers, json={"topic": "AI Trends"}
    )
    assert response.status_code == 403
