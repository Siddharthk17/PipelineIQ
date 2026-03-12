"""Pipeline scheduling API endpoints.

Provides CRUD for recurring pipeline schedules that are executed
automatically via Celery Beat.
"""

import logging
from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_db_dependency
from backend.models import PipelineSchedule, User
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import validate_uuid_format, as_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


class CreateScheduleRequest(BaseModel):
    """Request body to create a pipeline schedule."""

    pipeline_name: str = Field(..., max_length=255, description="Name for the scheduled pipeline")
    yaml_config: str = Field(..., min_length=10, description="YAML pipeline configuration")
    cron_expression: str = Field(..., description="Cron expression for scheduling (e.g. '*/5 * * * *')")

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        """Ensure the cron expression is valid."""
        try:
            croniter(value)
        except (ValueError, KeyError) as exc:
            raise ValueError(f"Invalid cron expression: {exc}") from exc
        return value


class ScheduleResponse(BaseModel):
    """Response for a pipeline schedule."""

    id: str
    pipeline_name: str
    cron_expression: str
    is_active: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str | None = None


def _compute_next_run(cron_expression: str) -> datetime:
    """Compute the next run time from a cron expression."""
    cron = croniter(cron_expression, datetime.now(timezone.utc))
    return cron.get_next(datetime).replace(tzinfo=timezone.utc)


def _schedule_to_response(schedule: PipelineSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=str(schedule.id),
        pipeline_name=schedule.pipeline_name,
        cron_expression=schedule.cron_expression,
        is_active=schedule.is_active,
        last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        created_at=schedule.created_at.isoformat() if schedule.created_at else None,
    )


@router.post(
    "/",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline schedule",
)
def create_schedule(
    request: Request,
    body: CreateScheduleRequest,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    """Create a new recurring pipeline schedule."""
    next_run = _compute_next_run(body.cron_expression)

    schedule = PipelineSchedule(
        user_id=current_user.id,
        pipeline_name=body.pipeline_name,
        yaml_config=body.yaml_config,
        cron_expression=body.cron_expression,
        is_active=True,
        next_run_at=next_run,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    log_action(db, "schedule_created", user_id=current_user.id,
               resource_type="schedule", resource_id=schedule.id,
               details={"pipeline_name": body.pipeline_name, "cron": body.cron_expression},
               request=request)

    logger.info("Schedule created: id=%s, pipeline=%s, cron=%s",
                schedule.id, body.pipeline_name, body.cron_expression)

    return _schedule_to_response(schedule)


@router.get(
    "/",
    summary="List user's pipeline schedules",
)
def list_schedules(
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List all schedules belonging to the authenticated user."""
    schedules = (
        db.query(PipelineSchedule)
        .filter(PipelineSchedule.user_id == current_user.id)
        .order_by(PipelineSchedule.created_at.desc())
        .all()
    )
    return {
        "schedules": [_schedule_to_response(s) for s in schedules],
        "total": len(schedules),
    }


@router.delete(
    "/{schedule_id}",
    summary="Delete a pipeline schedule",
)
def delete_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a pipeline schedule owned by the current user."""
    validate_uuid_format(schedule_id)
    schedule = (
        db.query(PipelineSchedule)
        .filter(
            PipelineSchedule.id == as_uuid(schedule_id),
            PipelineSchedule.user_id == current_user.id,
        )
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    db.delete(schedule)
    db.commit()

    log_action(db, "schedule_deleted", user_id=current_user.id,
               resource_type="schedule", resource_id=as_uuid(schedule_id),
               request=request)

    return {"detail": f"Schedule '{schedule_id}' deleted"}


@router.patch(
    "/{schedule_id}/toggle",
    response_model=ScheduleResponse,
    summary="Enable or disable a pipeline schedule",
)
def toggle_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    """Toggle the is_active flag of a pipeline schedule."""
    validate_uuid_format(schedule_id)
    schedule = (
        db.query(PipelineSchedule)
        .filter(
            PipelineSchedule.id == as_uuid(schedule_id),
            PipelineSchedule.user_id == current_user.id,
        )
        .first()
    )
    if not schedule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")

    schedule.is_active = not schedule.is_active
    if schedule.is_active:
        schedule.next_run_at = _compute_next_run(schedule.cron_expression)
    else:
        schedule.next_run_at = None
    db.commit()
    db.refresh(schedule)

    log_action(db, "schedule_toggled", user_id=current_user.id,
               resource_type="schedule", resource_id=schedule.id,
               details={"is_active": schedule.is_active}, request=request)

    return _schedule_to_response(schedule)
