import pytest
from unittest.mock import patch, MagicMock
from backend.core.config import settings

@pytest.mark.anyio
async def test_ollama_request_options():
    # We want to patch httpx.AsyncClient.post to capture calls from agent service
    # and also test the summary options. Wait, summary uses httpx.post (sync) maybe?
    # Let's import the functions.
    from backend.modules.agents.service import _call_ollama_blocking, chat_stream, chat_stream_deep, generate_report
    from backend.modules.events.summary import generate_summary
    from backend.modules.agents.schemas import ChatRequest, ReportRequest
    
    # We need to capture the json bodies
    bodies = []
    
    class AsyncMockResponse:
        def __init__(self, json_data=None):
            self._json = json_data or {"message": {"content": "ok"}}
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_lines(self):
            yield '{"message": {"content": "ok"}, "done": true}'
        def raise_for_status(self):
            pass
        def json(self):
            return self._json

    async def mock_async_post(*args, **kwargs):
        bodies.append(kwargs.get("json", {}))
        return AsyncMockResponse()
    
    class SyncMockResponse:
        def __init__(self, json_data=None):
            self._json = json_data or {"message": {"content": "ok"}}
        def raise_for_status(self):
            pass
        def json(self):
            return self._json
            
    def mock_sync_post(*args, **kwargs):
        bodies.append(kwargs.get("json", {}))
        return SyncMockResponse()
        
    with patch("backend.modules.agents.service.httpx.AsyncClient.post", new=mock_async_post), \
         patch("backend.modules.events.summary.httpx.post", new=mock_sync_post), \
         patch("backend.core.database.SessionLocal"):
        
        # 1. _call_ollama_blocking (used in planner, reasoner, synthesizer, report)
        await _call_ollama_blocking([{"role": "system", "content": "hello"}])
        
        # 2. chat_stream (fast path)
        request = ChatRequest(message="test fast", deep_path=False)
        try:
            async for _ in chat_stream(request, user_id=None, db=MagicMock()):
                pass
        except Exception:
            pass # ignore DB or other mock errors
            
        # 3. chat_stream_deep
        request_deep = ChatRequest(message="test deep", deep_path=True)
        try:
            async for _ in chat_stream_deep(request_deep, user_id=None, db=MagicMock()):
                pass
        except Exception:
            pass
            
        # 4. generate_report
        req_report = ReportRequest(topic="test")
        try:
            import asyncio; await asyncio.to_thread(generate_report, req_report, db=MagicMock(), user_id=None)
        except Exception:
            pass
            
        # 5. generate_summary
        class DummyArticle:
            title = "Title"
            def __init__(self):
                from backend.modules.retrieval.schemas import ChunkExcerpt
                self.chunks = [MagicMock(text="chunk")]
        try:
            generate_summary([DummyArticle()])
        except Exception:
            pass
            
    # Now verify all captured bodies have the correct options
    assert len(bodies) > 0
    for i, body in enumerate(bodies):
        options = body.get("options", {})
        assert options.get("num_ctx") == settings.chat_num_ctx, f"Missing/wrong num_ctx in call {i}: {options}"
        assert options.get("num_predict") == settings.chat_num_predict, f"Missing/wrong num_predict in call {i}: {options}"
