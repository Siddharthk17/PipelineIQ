"""Pipeline scheduling API endpoints.

Provides CRUD for recurring pipeline schedules that are executed
automatically via Celery Beat.
"""

import logging
import uuid
from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PipelineSchedule, ScheduleRun, User
from backend.scheduling.cron_utils import (
    cron_to_human,
    get_next_n_runs,
    get_next_run_at,
)
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/schedules", tags=["schedules"])


class CreateScheduleRequest(BaseModel):
    pipeline_name: str = Field(..., max_length=255,
                               description="Name for the scheduled pipeline")
    yaml_config: str = Field(..., min_length=10,
                             description="YAML pipeline configuration")
    cron_expression: str = Field(..., description="Cron expression (e.g. '0 6 * * 1')")

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Cron expression is required")

        normalized = value.strip().lower()

        conversions = {
            "every minute": "* * * * *",
            "every 5 minutes": "*/5 * * * *",
            "every 15 minutes": "*/15 * * * *",
            "every 30 minutes": "*/30 * * * *",
            "every hour": "0 * * * *",
            "hourly": "0 * * * *",
            "daily": "0 0 * * *",
            "midnight": "0 0 * * *",
            "weekly": "0 0 * * 0",
            "monthly": "0 0 1 * *",
        }
        if normalized in conversions:
            return conversions[normalized]

        if normalized.startswith("@"):
            cron_map = {
                "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *",
                "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
                "@daily": "0 0 * * *", "@midnight": "0 0 * * *",
                "@hourly": "0 * * * *",
            }
            if normalized in cron_map:
                return cron_map[normalized]

        try:
            croniter(value)
        except (ValueError, KeyError) as exc:
            raise ValueError(
                f"Invalid cron expression '{value}'. Use format like '*/5 * * * *', "
                "'0 * * * *' (hourly), '0 0 * * *' (daily)") from exc
        return value


class ScheduleResponse(BaseModel):
    id: str
    pipeline_name: str
    cron_expression: str
    cron_human: str | None = None
    is_active: bool
    last_run_at: str | None = None
    next_run_at: str | None = None
    last_run_status: str | None = None
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    healed_runs: int = 0
    created_at: str | None = None


def _compute_next_run(cron_expression: str) -> datetime:
    return get_next_run_at(cron_expression)


def _schedule_to_response(schedule: PipelineSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        id=str(schedule.id),
        pipeline_name=schedule.pipeline_name,
        cron_expression=schedule.cron_expression,
        cron_human=schedule.cron_human or cron_to_human(schedule.cron_expression),
        is_active=schedule.is_active,
        last_run_at=schedule.last_run_at.isoformat() if schedule.last_run_at else None,
        next_run_at=schedule.next_run_at.isoformat() if schedule.next_run_at else None,
        last_run_status=schedule.last_run_status,
        total_runs=schedule.total_runs or 0,
        successful_runs=schedule.successful_runs or 0,
        failed_runs=schedule.failed_runs or 0,
        healed_runs=schedule.healed_runs or 0,
        created_at=schedule.created_at.isoformat() if schedule.created_at else None,
    )


def _get_schedule_or_404(
    schedule_id: str, current_user: User, db: Session,
) -> PipelineSchedule:
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found")
    return schedule


@router.post(
    "/",
    response_model=ScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline schedule",
)
def create_schedule(
    request: Request,
    body: CreateScheduleRequest,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    next_run = _compute_next_run(body.cron_expression)
    human = cron_to_human(body.cron_expression)

    schedule = PipelineSchedule(
        user_id=current_user.id,
        pipeline_name=body.pipeline_name,
        yaml_config=body.yaml_config,
        cron_expression=body.cron_expression,
        cron_human=human,
        is_active=True,
        next_run_at=next_run,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)

    log_action(
        db, "schedule_created", user_id=current_user.id,
        resource_type="schedule", resource_id=schedule.id,
        details={"pipeline_name": body.pipeline_name, "cron": body.cron_expression},
        request=request)

    try:
        from backend.scheduling.beat_manager import register_schedules
        register_schedules()
    except Exception as exc:
        logger.warning("Schedule saved but Beat registration failed: %s", exc)

    logger.info("Schedule created: id=%s, pipeline=%s, cron=%s",
                schedule.id, body.pipeline_name, body.cron_expression)
    return _schedule_to_response(schedule)


@router.get("/", summary="List user's pipeline schedules")
def list_schedules(
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
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


@router.get("/{schedule_id}", response_model=ScheduleResponse)
def get_schedule(
    schedule_id: str,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)
    return _schedule_to_response(schedule)


@router.post(
    "/{schedule_id}/pause",
    response_model=ScheduleResponse,
    summary="Pause a pipeline schedule",
)
def pause_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)
    if not schedule.is_active:
        raise HTTPException(status_code=400, detail="Schedule is already paused")

    schedule.is_active = False
    schedule.next_run_at = None
    db.commit()
    db.refresh(schedule)

    try:
        from backend.scheduling.beat_manager import register_schedules
        register_schedules()
    except Exception as exc:
        logger.warning("Beat deregistration failed: %s", exc)

    return _schedule_to_response(schedule)


@router.post(
    "/{schedule_id}/resume",
    response_model=ScheduleResponse,
    summary="Resume a paused pipeline schedule",
)
def resume_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)
    if schedule.is_active:
        raise HTTPException(status_code=400, detail="Schedule is already active")

    schedule.is_active = True
    schedule.next_run_at = _compute_next_run(schedule.cron_expression)
    db.commit()
    db.refresh(schedule)

    try:
        from backend.scheduling.beat_manager import register_schedules
        register_schedules()
    except Exception as exc:
        logger.warning("Beat registration failed: %s", exc)

    return _schedule_to_response(schedule)


@router.delete(
    "/{schedule_id}",
    summary="Delete a pipeline schedule",
)
def delete_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)

    try:
        from backend.scheduling.beat_manager import register_schedules
        db.delete(schedule)
        db.commit()
        register_schedules()
    except Exception:
        db.delete(schedule)
        db.commit()

    log_action(db, "schedule_deleted", user_id=current_user.id,
               resource_type="schedule", resource_id=as_uuid(schedule_id),
               request=request)

    return {"detail": f"Schedule '{schedule_id}' deleted"}


@router.get("/{schedule_id}/runs", summary="Get schedule run history")
def list_schedule_runs(
    schedule_id: str,
    limit: int = 20,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)

    runs = (
        db.query(ScheduleRun)
        .filter(ScheduleRun.schedule_id == schedule.id)
        .order_by(ScheduleRun.triggered_at.desc())
        .limit(limit)
        .all()
    )

    return {
        "schedule_id": schedule_id,
        "runs": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id) if r.run_id else None,
                "triggered_at": r.triggered_at.isoformat(),
                "status": r.status,
                "duration_seconds": r.duration_seconds,
                "error_message": r.error_message,
            }
            for r in runs
        ],
    }


@router.get("/{schedule_id}/preview", summary="Preview next scheduled runs")
def preview_schedule(
    schedule_id: str,
    n: int = 5,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)
    next_runs = get_next_n_runs(schedule.cron_expression, n=n)
    return {
        "schedule_id": schedule_id,
        "cron_expression": schedule.cron_expression,
        "cron_human": schedule.cron_human or cron_to_human(schedule.cron_expression),
        "next_runs": [r.isoformat() for r in next_runs],
    }


@router.patch(
    "/{schedule_id}/toggle",
    response_model=ScheduleResponse,
    summary="Enable or disable a pipeline schedule (legacy)",
)
def toggle_schedule(
    schedule_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ScheduleResponse:
    schedule = _get_schedule_or_404(schedule_id, current_user, db)
    schedule.is_active = not schedule.is_active
    if schedule.is_active:
        schedule.next_run_at = _compute_next_run(schedule.cron_expression)
    else:
        schedule.next_run_at = None
    db.commit()
    db.refresh(schedule)

    try:
        from backend.scheduling.beat_manager import register_schedules
        register_schedules()
    except Exception:
        pass

    log_action(db, "schedule_toggled", user_id=current_user.id,
               resource_type="schedule", resource_id=schedule.id,
               details={"is_active": schedule.is_active}, request=request)

    return _schedule_to_response(schedule)
