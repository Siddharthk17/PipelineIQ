"""User dashboard and analytics API endpoints.

Provides personalized statistics and activity summaries for the
authenticated user's pipeline runs, files, and audit history.
"""

import logging
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency
from backend.models import AuditLog, PipelineRun, PipelineStatus, UploadedFile, User
from backend.utils.rate_limiter import limiter
from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get(
    "/stats",
    summary="Get user's personal dashboard statistics",
    description="Returns aggregated stats for the authenticated user's pipeline activity.",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_dashboard_stats(
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get dashboard statistics for the current user."""
    user_id = current_user.id

    # Total runs by this user
    total_runs = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id)
        .scalar() or 0
    )

    # Runs by status
    completed = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id, PipelineRun.status == PipelineStatus.COMPLETED)
        .scalar() or 0
    )
    failed = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id, PipelineRun.status == PipelineStatus.FAILED)
        .scalar() or 0
    )
    pending = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id, PipelineRun.status == PipelineStatus.PENDING)
        .scalar() or 0
    )
    running = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id, PipelineRun.status == PipelineStatus.RUNNING)
        .scalar() or 0
    )
    cancelled = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == user_id, PipelineRun.status == PipelineStatus.CANCELLED)
        .scalar() or 0
    )

    # Success rate
    success_rate = round(completed / total_runs * 100, 1) if total_runs > 0 else 0.0

    # Most used files (by pipeline name frequency)
    most_used_pipelines = (
        db.query(PipelineRun.name, func.count(PipelineRun.id).label("run_count"))
        .filter(PipelineRun.user_id == user_id)
        .group_by(PipelineRun.name)
        .order_by(func.count(PipelineRun.id).desc())
        .limit(5)
        .all()
    )

    # Recent activity from audit_logs
    recent_activity = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(10)
        .all()
    )

    # Total files uploaded (system-wide since files don't have user_id)
    total_files = db.query(func.count(UploadedFile.id)).scalar() or 0

    return {
        "total_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "running": running,
        "cancelled": cancelled,
        "success_rate": success_rate,
        "total_files": total_files,
        "pipelines_by_status": {
            "COMPLETED": completed,
            "FAILED": failed,
            "PENDING": pending,
            "RUNNING": running,
            "CANCELLED": cancelled,
        },
        "most_used_pipelines": [
            {"name": name, "run_count": count}
            for name, count in most_used_pipelines
        ],
        "recent_activity": [
            {
                "id": str(a.id),
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": str(a.resource_id) if a.resource_id else None,
                "details": a.details,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in recent_activity
        ],
    }
