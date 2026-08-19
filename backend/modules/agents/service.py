"""Agents service layer — Phase 5 chat and reports.

Implements:
  * FR-19: fast-path chat (single retrieve→generate→cite SSE stream)
  * FR-21: deep-path multi-step reasoning (planner→retriever→reasoner→synthesizer)
  * FR-22: evidence agreement scoring (mutual-support cosine across cited chunks)
  * Cost/token tracking via ``llm_usage`` table

Architecture note
-----------------
This module MUST NOT import from sibling business modules (retrieval, ranking,
events).  The ``agents/router.py`` integration layer calls ``retrieval.search()``
and passes the results in as ``context``.  The ``retrieval.schemas.SearchResult``
type is imported under ``TYPE_CHECKING`` only so the runtime import graph stays
clean for import-linter; the contract is also explicitly white-listed in
``.importlinter``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.db.models import (
    Conversation,
    ConversationMessage,
    LlmUsage,
    Report,
)
from backend.modules.agents.schemas import (
    ChatRequest,
    EvidenceItem,
    ReportRequest,
    ReportResponse,
)

if TYPE_CHECKING:
    from backend.modules.retrieval.schemas import SearchResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

_SYSTEM_FAST = (
    "You are a real-time news intelligence assistant. "
    "Use ONLY the provided context to answer the user's question. "
    "Cite every fact with the supplied inline citation IDs, e.g. [#1] or [#2]. "
    "Do not hallucinate information outside the context."
)

_SYSTEM_PLANNER = (
    "You are a research planning assistant. "
    "Given a complex question, output a numbered list of 2–4 focused sub-questions "
    "that together will fully answer the original question. "
    "Output ONLY the numbered list, no preamble."
)

_SYSTEM_REASONER = (
    "You are a news analyst. "
    "Given a sub-question and relevant article excerpts, write a concise answer "
    "(1–3 sentences) using ONLY the provided context. "
    "Cite facts with inline citation IDs like [#1]. "
    "Do not hallucinate."
)

_SYSTEM_SYNTHESIZER = (
    "You are a senior news intelligence analyst. "
    "Given partial answers to sub-questions and their sources, synthesise a single "
    "coherent, well-structured response to the original question. "
    "Preserve all inline citations from the partial answers. "
    "Do not add new facts."
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_line(data: dict) -> str:
    """Format a dict as an SSE ``data:`` line."""
    return "data: " + json.dumps(data) + "\n\n"


def _rough_tokens(text: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token (BPE average)."""
    return max(1, len(text) // 4)


def _log_usage(
    db: Session,
    *,
    user_id: uuid.UUID | None,
    operation: str,
    prompt: str,
    response: str,
    latency_ms: int,
) -> None:
    """Write one row to ``llm_usage``; errors are swallowed (non-critical)."""
    try:
        row = LlmUsage(
            user_id=user_id,
            operation=operation,
            model=settings.chat_model,
            input_tokens=_rough_tokens(prompt),
            output_tokens=_rough_tokens(response),
            latency_ms=latency_ms,
        )
        db.add(row)
        db.flush()
    except Exception:
        logger.exception("Failed to log LLM usage (non-critical)")


def _compute_agreement(evidence: list[EvidenceItem]) -> float:
    """FR-22: evidence agreement score.

    For each pair of citations, if their titles share a meaningful word overlap
    (Jaccard ≥ 0.10) we count them as mutually supportive.  The score is the
    fraction of citations that have at least one mutual supporter.

    This is a lightweight lexical proxy — no embedding calls needed — that gives
    a sensible 0.0–1.0 signal without requiring the retrieval module at runtime.
    """
    if len(evidence) <= 1:
        return 1.0  # single source, no contradiction possible

    def _tokens(title: str) -> set[str]:
        stopwords = {"the", "a", "an", "of", "in", "and", "to", "for", "on", "at", "is"}
        return {w.lower() for w in title.split() if len(w) > 2 and w.lower() not in stopwords}

    supported: set[int] = set()
    for i, ei in enumerate(evidence):
        ti = _tokens(ei.title)
        for j, ej in enumerate(evidence):
            if i == j:
                continue
            tj = _tokens(ej.title)
            if not ti or not tj:
                continue
            jaccard = len(ti & tj) / len(ti | tj)
            if jaccard >= 0.10:
                supported.add(i)
                supported.add(j)

    return len(supported) / len(evidence)


async def _call_ollama_blocking(prompt_messages: list[dict], *, label: str = "") -> str:
    """Non-streaming Ollama call (used for planner/reasoner/synthesizer stages).

    Raises ``httpx.HTTPError`` on failure; callers handle degradation.
    """
    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.ollama_url}/api/chat",
            json={
                "model": settings.chat_model,
                "messages": prompt_messages,
                "stream": False,
                "think": False,
                "options": {
                    "num_predict": settings.chat_max_tokens,
                    "temperature": 0.3,
                },
            },
            timeout=settings.chat_timeout_seconds,
        )
        response.raise_for_status()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        data = response.json()
        content = (data.get("message", {}).get("content") or "").strip()
        logger.debug("Ollama %s: %d chars in %dms", label, len(content), elapsed_ms)
        return content


# ---------------------------------------------------------------------------
# Complexity heuristic (FR-21 routing)
# ---------------------------------------------------------------------------


def _is_complex(message: str) -> bool:
    """Return True if the question warrants deep-path multi-step reasoning.

    Heuristics (any one triggers deep path):
    - More than 30 words
    - Contains comparison/analysis keywords
    - Contains multiple clauses joined by "and"/"or" with commas
    """
    words = message.split()
    if len(words) > 30:
        return True
    lower = message.lower()
    triggers = [
        "compare",
        "contrast",
        "analyse",
        "analyze",
        "why ",
        "how does",
        "what are the reasons",
        "what caused",
        "relationship between",
        "impact of",
        "effect of",
        "explain the",
        "difference between",
    ]
    if any(t in lower for t in triggers):
        return True
    # Multiple independent clauses (commas + "and")
    return bool("," in message and " and " in lower)


# ---------------------------------------------------------------------------
# Fast path (FR-19 / FR-20)
# ---------------------------------------------------------------------------


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

    # Build context + evidence list
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

    context_text = "Context:\n" + "\n".join(context_parts)
    user_prompt = context_text + "\n\nQuestion: " + request.message

    assistant_content = ""
    start_ts = time.monotonic()

    if settings.chat_provider == "none":
        yield _sse_line({"error": "Chat provider disabled"})
        return

    # --- Stream tokens from Ollama ---
    async with httpx.AsyncClient() as client:
        try:
            async with client.stream(
                "POST",
                f"{settings.ollama_url}/api/chat",
                json={
                    "model": settings.chat_model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_FAST},
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
                            yield _sse_line({"type": "token", "token": token})
                    except json.JSONDecodeError:
                        logger.warning("Could not decode SSE line from Ollama")

        except httpx.HTTPError as exc:
            logger.warning("Ollama fast-path chat failed: %s", exc)
            yield _sse_line({"type": "error", "error": "Chat service unavailable"})
            _persist_message(
                db,
                conversation_id=conversation_id,
                content="",
                evidence_list=evidence_list,
                agreement=0.0,
                error=True,
            )
            return

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)

    # FR-22: agreement score
    agreement = _compute_agreement(evidence_list)

    # Log usage
    _log_usage(
        db,
        user_id=user_id,
        operation="chat_fast",
        prompt=user_prompt,
        response=assistant_content,
        latency_ms=elapsed_ms,
    )

    # Final SSE event with full evidence payload
    evidence_dicts = [e.model_dump(mode="json") for e in evidence_list]
    yield _sse_line(
        {
            "type": "evidence",
            "message": assistant_content,
            "conversation_id": str(conversation_id),
            "evidence": evidence_dicts,
            "agreement": round(agreement, 4),
        }
    )

    _persist_message(
        db,
        conversation_id=conversation_id,
        content=assistant_content,
        evidence_list=evidence_list,
        agreement=agreement,
    )


# ---------------------------------------------------------------------------
# Deep path (FR-21)
# ---------------------------------------------------------------------------


async def chat_stream_deep(
    db: Session,
    user_id: uuid.UUID,
    request: ChatRequest,
    search_fn,  # callable(query: str, limit: int) -> list[SearchResult]
) -> AsyncGenerator[str]:
    """Deep-path multi-step reasoning: planner→retriever→reasoner→synthesizer.

    ``search_fn`` is injected by the router so this service never imports retrieval.
    """
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
    db.add(
        ConversationMessage(
            conversation_id=conversation_id,
            role="user",
            content=request.message,
        )
    )
    db.flush()

    all_evidence: list[EvidenceItem] = []
    partial_answers: list[str] = []
    citation_offset = 0
    start_ts = time.monotonic()

    # ── Stage 1: Planner ──────────────────────────────────────────────────
    yield _sse_line({"type": "thinking", "stage": "planner", "message": "Decomposing question…"})
    try:
        plan_text = await _call_ollama_blocking(
            [
                {"role": "system", "content": _SYSTEM_PLANNER},
                {"role": "user", "content": request.message},
            ],
            label="planner",
        )
    except httpx.HTTPError as exc:
        logger.warning("Planner Ollama call failed: %s", exc)
        # Fall back to fast path
        async for chunk in chat_stream(db, user_id, request, context=[]):
            yield chunk
        return

    # Parse sub-questions (strip leading "1. ", "2. " etc.)
    sub_questions = [
        line.lstrip("0123456789. ").strip()
        for line in plan_text.splitlines()
        if line.strip() and line.strip()[0].isdigit()
    ]
    if not sub_questions:
        sub_questions = [request.message]  # degenerate fallback
    sub_questions = sub_questions[:4]  # cap at 4

    yield _sse_line(
        {
            "type": "thinking",
            "stage": "planner",
            "sub_questions": sub_questions,
        }
    )

    _log_usage(
        db,
        user_id=user_id,
        operation="chat_deep",
        prompt=request.message,
        response=plan_text,
        latency_ms=0,
    )

    # ── Stages 2 & 3: Retriever + Reasoner (per sub-question) ────────────
    for idx, sub_q in enumerate(sub_questions):
        yield _sse_line(
            {
                "type": "thinking",
                "stage": "reasoner",
                "sub_question": sub_q,
                "index": idx,
            }
        )

        # Retrieval (injected fn)
        try:
            sub_results: list[SearchResult] = search_fn(query=sub_q, limit=5)
        except Exception as exc:
            logger.warning("Retrieval failed for sub-question %d: %s", idx, exc)
            sub_results = []

        # Build local evidence slice with global citation IDs
        local_evidence: list[EvidenceItem] = []
        context_parts: list[str] = []
        for result in sub_results:
            citation_offset += 1
            item = EvidenceItem(
                citation_id=citation_offset,
                article_id=result.article_id,
                title=result.title,
                source_id=result.source_id,
                published_at=result.published_at,
                score=result.similarity_score,
            )
            local_evidence.append(item)
            all_evidence.append(item)
            context_parts.append(f"[#{citation_offset}] Title: {result.title}")

        sub_context = "Context:\n" + "\n".join(context_parts) if context_parts else "(no context)"
        reasoner_prompt = f"{sub_context}\n\nSub-question: {sub_q}"

        try:
            partial = await _call_ollama_blocking(
                [
                    {"role": "system", "content": _SYSTEM_REASONER},
                    {"role": "user", "content": reasoner_prompt},
                ],
                label=f"reasoner[{idx}]",
            )
        except httpx.HTTPError as exc:
            logger.warning("Reasoner failed for sub-question %d: %s", idx, exc)
            partial = f"(could not answer sub-question: {sub_q})"

        partial_answers.append(f"Sub-question {idx + 1}: {sub_q}\nAnswer: {partial}")

        _log_usage(
            db,
            user_id=user_id,
            operation="chat_deep",
            prompt=reasoner_prompt,
            response=partial,
            latency_ms=0,
        )

    # ── Stage 4: Synthesizer ──────────────────────────────────────────────
    yield _sse_line({"type": "thinking", "stage": "synthesizer", "message": "Synthesising…"})

    synthesis_prompt = f"Original question: {request.message}\n\n" + "\n\n".join(partial_answers)
    try:
        final_content = await _call_ollama_blocking(
            [
                {"role": "system", "content": _SYSTEM_SYNTHESIZER},
                {"role": "user", "content": synthesis_prompt},
            ],
            label="synthesizer",
        )
    except httpx.HTTPError as exc:
        logger.warning("Synthesizer failed: %s", exc)
        final_content = "\n\n".join(partial_answers)

    elapsed_ms = int((time.monotonic() - start_ts) * 1000)

    _log_usage(
        db,
        user_id=user_id,
        operation="chat_deep",
        prompt=synthesis_prompt,
        response=final_content,
        latency_ms=elapsed_ms,
    )

    # FR-22: agreement
    agreement = _compute_agreement(all_evidence)

    # Stream final content token-by-token for a consistent UX
    for token in final_content.split(" "):
        yield _sse_line({"type": "token", "token": token + " "})

    evidence_dicts = [e.model_dump(mode="json") for e in all_evidence]
    yield _sse_line(
        {
            "type": "evidence",
            "message": final_content,
            "conversation_id": str(conversation_id),
            "evidence": evidence_dicts,
            "agreement": round(agreement, 4),
        }
    )

    _persist_message(
        db,
        conversation_id=conversation_id,
        content=final_content,
        evidence_list=all_evidence,
        agreement=agreement,
    )


# ---------------------------------------------------------------------------
# Shared DB helper
# ---------------------------------------------------------------------------


def _persist_message(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    content: str,
    evidence_list: list[EvidenceItem],
    agreement: float,
    error: bool = False,
) -> None:
    try:
        evidence_dicts = [e.model_dump(mode="json") for e in evidence_list]
        msg = ConversationMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            evidence={"items": evidence_dicts, **({"error": True} if error else {})},
            evidence_agreement=agreement,
        )
        db.add(msg)
        db.commit()
    except Exception:
        logger.exception("Failed to persist chat message (non-critical)")


# ---------------------------------------------------------------------------
# Executive report generation
# ---------------------------------------------------------------------------


def generate_report(
    db: Session,
    user_id: uuid.UUID,
    request: ReportRequest,
    search_fn,  # callable(query: str, limit: int) -> list[SearchResult]
) -> ReportResponse:
    """Generate an executive report using the same retrieve→generate pipeline.

    The 'deep path' for reports: retrieve top articles for the topic, build a
    structured report prompt, call Ollama synchronously, persist with evidence.
    """
    import asyncio

    report = Report(
        user_id=user_id,
        topic=request.topic,
        timeframe=request.timeframe,
        status="pending",
    )
    db.add(report)
    db.commit()

    try:
        results: list[SearchResult] = search_fn(
            query=request.topic + (" " + request.timeframe if request.timeframe else ""),
            limit=10,
        )

        evidence_list: list[EvidenceItem] = []
        context_parts: list[str] = []
        for i, r in enumerate(results, 1):
            evidence_list.append(
                EvidenceItem(
                    citation_id=i,
                    article_id=r.article_id,
                    title=r.title,
                    source_id=r.source_id,
                    published_at=r.published_at,
                    score=r.similarity_score,
                )
            )
            context_parts.append(f"[#{i}] {r.title}")

        timeframe_str = f" over {request.timeframe}" if request.timeframe else ""
        context_block = "\n".join(context_parts) if context_parts else "(no articles retrieved)"
        report_prompt = (
            f"Write an executive intelligence report on: {request.topic}{timeframe_str}\n\n"
            f"Available sources:\n{context_block}\n\n"
            "Structure the report with: Executive Summary, Key Developments, "
            "Analysis, and Outlook. Cite every claim with inline citation IDs."
        )

        _system_report = (
            "You are a senior intelligence analyst. "
            "Produce a concise, structured executive report from the provided sources. "
            "Cite every fact with inline citation IDs like [#1]. "
            "Do not hallucinate."
        )

        start_ts = time.monotonic()
        summary = asyncio.run(
            _call_ollama_blocking(
                [
                    {"role": "system", "content": _system_report},
                    {"role": "user", "content": report_prompt},
                ],
                label="report",
            )
        )
        elapsed_ms = int((time.monotonic() - start_ts) * 1000)

        agreement = _compute_agreement(evidence_list)

        _log_usage(
            db,
            user_id=user_id,
            operation="report",
            prompt=report_prompt,
            response=summary,
            latency_ms=elapsed_ms,
        )

        report.status = "completed"
        report.content = {
            "summary": summary,
            "sources": [e.model_dump(mode="json") for e in evidence_list],
        }
        report.evidence_agreement = {
            "score": round(agreement, 4),
            "method": "lexical_jaccard",
            "sources_checked": len(evidence_list),
            "sources_agreeing": int(agreement * len(evidence_list)),
            "contradictions": [],
        }

    except Exception as exc:
        logger.warning("Report generation failed: %s", exc)
        report.status = "failed"
        report.content = {"error": str(exc)}
        report.evidence_agreement = {
            "score": 0.0,
            "method": "none",
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
