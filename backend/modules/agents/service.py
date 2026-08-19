import json
import logging
import uuid
from collections.abc import AsyncGenerator

import httpx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import (
    Conversation,
    ConversationMessage,
    Report,
)
from backend.modules.agents.schemas import (
    ChatRequest,
    EvidenceItem,
    ReportRequest,
    ReportResponse,
)
from backend.modules.retrieval.schemas import SearchResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a real-time news intelligence assistant. "
    "Use the provided context to answer the user's question. "
    "You MUST cite your sources inline using the provided "
    "citation IDs in square brackets, like [#1] or [#2]. "
    "Do not hallucinate facts outside the context."
)


def _sse_line(data: dict) -> str:
    """Format a dict as an SSE ``data:`` line."""
    return "data: " + json.dumps(data) + chr(10) + chr(10)


async def chat_stream(
    db: Session,
    user_id: uuid.UUID,
    request: ChatRequest,
    context: list[SearchResult],
) -> AsyncGenerator[str]:
    """Fast-path chat using Ollama with SSE streaming and evidence attribution."""

    if request.conversation_id:
        conv = db.get(Conversation, request.conversation_id)
        if not conv or conv.user_id != user_id:
            yield _sse_line({"error": "Conversation not found"})
            return
        conversation_id = conv.id
    else:
        conv = Conversation(user_id=user_id, title=request.message[:50])
        db.add(conv)
        db.flush()
        conversation_id = conv.id

    # Save user message
    user_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="user",
        content=request.message,
    )
    db.add(user_msg)
    db.flush()

    # Build context prompt and evidence list
    evidence_list: list[EvidenceItem] = []
    context_parts: list[str] = []
    for i, result in enumerate(context, 1):
        evidence_list.append(
            EvidenceItem(
                citation_id=i,
                article_id=result.article_id,
                title=result.title,
                source_id=result.source_id,
                published_at=result.published_at,
                score=result.similarity_score,
            )
        )
        context_parts.append(f"[#{i}] Title: {result.title}")

    context_text = "Context:" + chr(10) + chr(10).join(context_parts)
    user_prompt = context_text + chr(10) + chr(10) + "Question: " + request.message

    assistant_content = ""

    # Stream from LLM provider
    if settings.chat_provider == "none":
        yield _sse_line({"error": "Chat provider disabled"})
        return
    elif settings.chat_provider == "ollama":
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream(
                    "POST",
                    f"{settings.ollama_url}/api/chat",
                    json={
                        "model": settings.chat_model,
                        "messages": [
                            {"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": user_prompt},
                        ],
                        "stream": True,
                        "think": False,
                        "options": {
                            "num_predict": settings.chat_max_tokens,
                            "temperature": 0.3,
                        },
                    },
                    timeout=settings.chat_timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            token = data.get("message", {}).get("content", "")
                            if token:
                                assistant_content += token
                                yield _sse_line({"token": token})
                        except json.JSONDecodeError:
                            logger.warning("Failed to decode JSON from streaming response")

            except httpx.HTTPError as exc:
                logger.warning("Ollama chat request failed: %s", exc)
                yield _sse_line({"error": "Chat service unavailable"})
                # Yield final payload with error state so client
                # gets a clean close
                evidence_dicts = [e.model_dump(mode="json") for e in evidence_list]
                final_payload = {
                    "message": "",
                    "conversation_id": str(conversation_id),
                    "evidence": evidence_dicts,
                    "error": True,
                }
                yield _sse_line(final_payload)
                # Save partial record
                asst_msg = ConversationMessage(
                    conversation_id=conversation_id,
                    role="assistant",
                    content="",
                    evidence={
                        "items": evidence_dicts,
                        "error": "Ollama unavailable",
                    },
                )
                db.add(asst_msg)
                db.commit()
                return

    # Final evidence yield
    evidence_dicts = [e.model_dump(mode="json") for e in evidence_list]
    final_payload = {
        "message": assistant_content,
        "conversation_id": str(conversation_id),
        "evidence": evidence_dicts,
    }
    yield _sse_line(final_payload)

    # Save assistant message
    asst_msg = ConversationMessage(
        conversation_id=conversation_id,
        role="assistant",
        content=assistant_content,
        evidence={"items": evidence_dicts},
    )
    db.add(asst_msg)
    db.commit()


def generate_report(
    db: Session,
    user_id: uuid.UUID,
    request: ReportRequest,
) -> ReportResponse:
    """Deep-path report generation (Stub)."""
    report = Report(
        user_id=user_id,
        topic=request.topic,
        timeframe=request.timeframe,
        status="pending",
    )
    db.add(report)
    db.commit()

    # Stub: update to completed
    report.status = "completed"
    report.content = {"summary": "This is a stub executive report."}
    report.evidence_agreement = {
        "score": 1.0,
        "method": "stub",
        "sources_checked": 0,
        "sources_agreeing": 0,
        "contradictions": [],
    }
    db.commit()

    return ReportResponse(
        id=report.id,
        topic=report.topic,
        status=report.status,
        created_at=report.created_at,
    )
