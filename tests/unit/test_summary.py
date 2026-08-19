"""Tests for the abstractive event summary generation (Ollama backend)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from backend.db.models import Article
from backend.modules.events.summary import (
    _build_user_prompt,
    generate_summary,
)


def _make_article(title: str = "Test Article", preview: str = "A test preview.") -> MagicMock:
    art = MagicMock(spec=Article)
    art.title = title
    art.content_preview = preview
    art.description = None
    return art


class TestBuildUserPrompt:
    def test_basic(self):
        articles = [_make_article("Title A", "Preview A"), _make_article("Title B", "Preview B")]
        prompt = _build_user_prompt(articles)
        assert "Title A" in prompt
        assert "Preview A" in prompt
        assert "Title B" in prompt
        assert "Articles:" in prompt

    def test_single_article(self):
        articles = [_make_article("Solo", "Solo preview")]
        prompt = _build_user_prompt(articles)
        assert "1. Solo" in prompt

    def test_missing_fields(self):
        art = MagicMock(spec=Article)
        art.title = None
        art.content_preview = None
        art.description = None
        prompt = _build_user_prompt([art])
        assert "(untitled)" in prompt

    def test_long_preview_truncated(self):
        articles = [_make_article("T", "x" * 500)]
        prompt = _build_user_prompt(articles)
        assert "x" * 200 in prompt
        assert "x" * 201 not in prompt


class TestGenerateSummary:
    @patch("backend.modules.events.summary.httpx.post")
    def test_success(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "A concise summary."}},
            raise_for_status=lambda: None,
        )
        articles = [_make_article(), _make_article()]
        result = generate_summary(articles)
        assert result == "A concise summary."
        assert mock_post.called
        call_json = mock_post.call_args[1]["json"]
        assert call_json["model"] == "qwen3.5:9b"
        assert call_json["messages"][0]["role"] == "system"

    @patch("backend.modules.events.summary.httpx.post")
    def test_empty_response(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": ""}},
            raise_for_status=lambda: None,
        )
        result = generate_summary([_make_article(), _make_article()])
        assert result is None

    def test_none_provider(self):
        from backend.core.config import settings

        original = settings.summary_provider
        try:
            settings.summary_provider = "none"
            result = generate_summary([_make_article(), _make_article()])
            assert result is None
        finally:
            settings.summary_provider = original

    def test_single_article_skips(self):
        result = generate_summary([_make_article()])
        assert result is None

    @patch("backend.modules.events.summary.httpx.post")
    def test_http_error_returns_none(self, mock_post):
        mock_post.side_effect = httpx.HTTPError("connection refused")
        articles = [_make_article(), _make_article()]
        result = generate_summary(articles)
        assert result is None

    @patch("backend.modules.events.summary.httpx.post")
    def test_timeout_returns_none(self, mock_post):
        mock_post.side_effect = httpx.TimeoutException("timeout")
        result = generate_summary([_make_article(), _make_article()])
        assert result is None

    @patch("backend.modules.events.summary.httpx.post")
    def test_key_extraction(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"message": {"content": "OK"}},
            raise_for_status=lambda: None,
        )
        generate_summary([_make_article(), _make_article()])
        body = mock_post.call_args[1]["json"]
        assert body["stream"] is False
        assert body["options"]["num_predict"] == 300
        assert body["options"]["temperature"] == 0.3
        msgs = body["messages"]
        assert msgs[1]["role"] == "user"
        assert "Test Article" in msgs[1]["content"]
