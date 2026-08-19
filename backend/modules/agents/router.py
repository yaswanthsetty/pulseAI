from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.db.models import Report, User
from backend.modules.agents import schemas
from backend.modules.agents import service as agent_service
from backend.modules.auth.deps import require_role
from backend.modules.retrieval import service as retrieval_service

router = APIRouter(tags=["Agents"])


@router.post("/chat")
async def chat(
    request: schemas.ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("user")),
):
    """Chat fast path with inline citations and streaming response."""
    results = retrieval_service.search(query=request.message, limit=5)

    return StreamingResponse(
        agent_service.chat_stream(db, user.id, request, context=results),
        media_type="text/event-stream",
    )


@router.post("/reports/generate", response_model=schemas.ReportResponse)
def generate_report(
    request: schemas.ReportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("analyst")),
):
    """Executive report generation (deep path stub)."""
    return agent_service.generate_report(db, user.id, request)


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
    """Get a specific report."""
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
