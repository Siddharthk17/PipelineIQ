"""Pipeline run step timing endpoint — matches Week 11 roadmap spec.

GET /api/runs/{run_id}/timing — per-step timing data for Gantt chart
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import PipelineRun, StepResult, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/runs", tags=["Runs"])


@router.get("/{run_id}/timing")
def get_run_timing(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Run not found")

    steps = (
        db.query(StepResult)
        .filter(StepResult.pipeline_run_id == run.id)
        .order_by(StepResult.step_index)
        .all()
    )

    return {
        "run_id": run_id,
        "steps": [
            {
                "step_name": s.step_name or "",
                "step_type": s.step_type or "",
                "engine": s.engine or "unknown",
                "start_at": s.started_at.isoformat() if s.started_at else None,
                "end_at": s.completed_at.isoformat() if s.completed_at else None,
                "duration_ms": s.duration_ms or 0,
                "row_in": s.row_in,
                "row_out": s.row_out,
                "span_id": s.span_id,
                "trace_id": s.trace_id,
            }
            for s in steps
        ],
    }
