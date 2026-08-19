"""LLM-backed abstractive event summary generation (FR-17).

Generates a concise summary of an event from its member articles using a
local Ollama model.  Falls back gracefully: if the LLM is unavailable or
times out, the extractive preview (already stored by the slow path) is kept.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from backend.core.config import settings

if TYPE_CHECKING:
    from backend.db.models import Article

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a concise news summarizer. Given a list of news article titles "
    "and their content previews, write a single cohesive paragraph (2-4 "
    "sentences) that captures the key facts, who is involved, and why it "
    "matters. Be specific with names, numbers, and dates. Do not hallucinate "
    "information not present in the input. Write in neutral journalistic tone."
)


def _build_user_prompt(articles: list[Article]) -> str:
    """Format the article list into a prompt for the LLM."""
    parts: list[str] = []
    for i, art in enumerate(articles, 1):
        title = art.title or "(untitled)"
        preview = (art.content_preview or art.description or "")[:200]
        parts.append(f"{i}. {title}\n   {preview}")
    return "Articles:\n" + "\n".join(parts)


def generate_summary(articles: list[Article]) -> str | None:
    """Call Ollama to produce an abstractive summary of the event.

    Returns the generated summary string, or ``None`` if the LLM is
    unavailable, errors, or produces empty output.  The caller decides
    whether to keep the extractive fallback or retry later.
    """
    if settings.summary_provider == "none" or len(articles) < 2:
        return None

    user_prompt = _build_user_prompt(articles)

    try:
        response = httpx.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": settings.summary_model,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "think": False,  # disable thinking mode for Qwen 3.5+
                "options": {
                    "num_predict": settings.summary_max_tokens,
                    "temperature": 0.3,
                },
            },
            timeout=settings.summary_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        summary = (data.get("message", {}).get("content") or "").strip()
        if summary:
            logger.info(
                "LLM summary generated (%d chars, model=%s)",
                len(summary),
                settings.summary_model,
            )
            return summary
        logger.warning("LLM returned empty summary for %d articles", len(articles))
        return None
    except httpx.HTTPError as exc:
        logger.warning("Ollama summary request failed: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected error during summary generation")
        return None
