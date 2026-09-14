"""Agents router — integration layer (spec §8 top-layer contract).

This module is the ONLY place allowed to import from sibling business modules
(retrieval.service).  It fetches context and injects it into the pure service
functions so that ``agents/service.py`` stays boundary-clean.
"""

from __future__ import annotations

import csv
import io
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import LlmUsage, Report, User
from backend.modules.agents import schemas
from backend.modules.agents import service as agent_service
from backend.modules.auth.deps import require_role
from backend.modules.retrieval import service as retrieval_service

router = APIRouter(tags=["Agents"])


# ---------------------------------------------------------------------------
# Chat endpoint (fast path + deep path)
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    request: schemas.ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("user")),
):
    """Chat endpoint — auto-routes to fast or deep path based on complexity.

    **Fast path** (FR-19/FR-20): single retrieve→generate→cite SSE stream.
    **Deep path** (FR-21): planner→retriever×N→reasoner×N→synthesizer SSE stream
    with ``thinking`` progress events.

    SSE event types emitted:
    * ``{"type": "thinking", "stage": "planner"|"reasoner"|"synthesizer", ...}``
    * ``{"type": "token", "token": "..."}``
    * ``{"type": "evidence", "message": "...", "evidence": [...], "agreement": 0.85, ...}``
    * ``{"type": "error", "error": "..."}``
    """
    if agent_service._is_complex(request.message):
        # Deep path: inject the search callable so service stays boundary-clean
        def _search(query: str, limit: int):
            return retrieval_service.search(query=query, limit=limit)

        return StreamingResponse(
            agent_service.chat_stream_deep(db, user.id, request, search_fn=_search),
            media_type="text/event-stream",
        )

    # Fast path: pre-fetch context here (integration layer), pass into service
    results = retrieval_service.search(query=request.message, limit=5)
    return StreamingResponse(
        agent_service.chat_stream(db, user.id, request, context=results),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@router.post("/reports/generate", response_model=schemas.ReportResponse)
def generate_report(
    request: schemas.ReportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("analyst")),
):
    """Executive report generation (deep path — retrieve→generate with citations)."""

    def _search(query: str, limit: int):
        return retrieval_service.search(query=query, limit=limit)

    return agent_service.generate_report(db, user.id, request, search_fn=_search)


@router.get("/reports")
def list_reports(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("analyst")),
):
    """List generated reports for the current user."""
    reports = (
        db.query(Report).filter(Report.user_id == user.id).order_by(Report.created_at.desc()).all()
    )
    items = [
        {"id": r.id, "topic": r.topic, "status": r.status, "created_at": r.created_at}
        for r in reports
    ]
    return {"items": items}


@router.get("/reports/{report_id}")
def get_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("analyst")),
):
    """Get a specific report by ID."""
    report = db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")

    return {
        "id": report.id,
        "topic": report.topic,
        "status": report.status,
        "content": report.content,
        "evidence_agreement": report.evidence_agreement,
        "created_at": report.created_at,
    }


@router.get("/reports/{report_id}/export")
def export_report(
    report_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("analyst")),
):
    """Export a report as CSV (executive hand-off; spec §5 report output)."""
    report = db.get(Report, report_id)
    if not report or report.user_id != user.id:
        raise HTTPException(status_code=404, detail="Report not found")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["field", "value"])
    writer.writerow(["id", str(report.id)])
    writer.writerow(["topic", report.topic])
    writer.writerow(["timeframe", report.timeframe or ""])
    writer.writerow(["status", report.status])
    writer.writerow(["created_at", report.created_at.isoformat() if report.created_at else ""])

    content = report.content
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError, TypeError:
            content = {"summary": content}
    if isinstance(content, dict):
        evidence = content.pop("evidence", None)
        for key, value in content.items():
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            writer.writerow([key, value])
        if isinstance(evidence, list):
            writer.writerow([])
            writer.writerow(["citation_id", "title", "source", "published_at", "score"])
            for item in evidence:
                if isinstance(item, dict):
                    writer.writerow(
                        [
                            item.get("citation_id", ""),
                            (item.get("title") or "").replace("\n", " ")[:300],
                            item.get("source", ""),
                            item.get("published_at") or "",
                            item.get("score", ""),
                        ]
                    )
    elif content is not None:
        writer.writerow(["content", str(content)])

    buffer.seek(0)
    filename = f"pulseai-report-{str(report.id)[:8]}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Usage / cost tracking  (GET /api/v1/usage)
# ---------------------------------------------------------------------------


@router.get("/usage")
def get_usage(
    operation: str | None = Query(default=None, description="Filter by operation type"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("user")),
):
    """Token usage summary.

    * Regular users see only their own usage.
    * Admins see all users' usage (pass ``user_id`` to the DB query).
    """
    is_admin = user.role == "admin"

    # Build aggregate query
    base = select(
        LlmUsage.operation,
        LlmUsage.model,
        func.count(LlmUsage.id).label("calls"),
        func.sum(LlmUsage.input_tokens).label("input_tokens"),
        func.sum(LlmUsage.output_tokens).label("output_tokens"),
        func.sum(LlmUsage.input_tokens + LlmUsage.output_tokens).label("total_tokens"),
        func.avg(LlmUsage.latency_ms).label("avg_latency_ms"),
    )

    if not is_admin:
        base = base.where(LlmUsage.user_id == user.id)
    if operation:
        base = base.where(LlmUsage.operation == operation)

    base = base.group_by(LlmUsage.operation, LlmUsage.model)
    rows = db.execute(base).all()

    breakdown = [
        {
            "operation": r.operation,
            "model": r.model,
            "calls": r.calls,
            "input_tokens": int(r.input_tokens or 0),
            "output_tokens": int(r.output_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
            "avg_latency_ms": round(float(r.avg_latency_ms or 0), 1),
        }
        for r in rows
    ]

    return {
        "user_id": str(user.id) if not is_admin else None,
        "scope": "all" if is_admin else "own",
        "breakdown": breakdown,
        "total_tokens": sum(b["total_tokens"] for b in breakdown),
    }
