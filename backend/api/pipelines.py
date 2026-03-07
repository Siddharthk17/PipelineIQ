"""Pipeline execution API endpoints with SSE streaming.

Provides endpoints for pipeline validation, execution (async via Celery),
result retrieval, and real-time progress streaming via Server-Sent Events.
"""

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_optional_user
from backend.config import settings
from backend.dependencies import get_db_dependency
from backend.models import PipelineRun, PipelineStatus, UploadedFile, User
from datetime import timezone
from backend.pipeline.parser import PipelineParser
from backend.pipeline.planner import generate_execution_plan
from backend.schemas import (
    PipelineRunListResponse,
    PipelineRunResponse,
    RunPipelineRequest,
    RunPipelineResponse,
    StepResultResponse,
    ValidatePipelineRequest,
    ValidatePipelineResponse,
    ValidationErrorDetail,
    ValidationWarningDetail,
)
from backend.tasks.pipeline_tasks import execute_pipeline_task
from backend.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)


def _as_uuid(val):
    """Convert str or uuid.UUID to uuid.UUID for DB queries."""
    return val if isinstance(val, uuid.UUID) else uuid.UUID(val)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

# Terminal event types that signal the SSE stream should close
_TERMINAL_EVENT_TYPES = frozenset({
    "pipeline_completed",
    "pipeline_failed",
    "COMPLETED",
    "FAILED",
})

@router.post(
    "/validate",
    response_model=ValidatePipelineResponse,
    summary="Validate pipeline configuration",
    description="Validate a YAML pipeline configuration without executing it.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def validate_pipeline(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ValidatePipelineResponse:
    """Validate a pipeline configuration against registered files."""
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)

    registered_ids = {str(f.id) for f in db.query(UploadedFile).all()}
    result = parser.validate(config, registered_ids)

    return ValidatePipelineResponse(
        is_valid=result.is_valid,
        errors=[
            ValidationErrorDetail(
                step_name=e.step_name,
                field=e.field,
                message=e.message,
                suggestion=e.suggestion,
            )
            for e in result.errors
        ],
        warnings=[
            ValidationWarningDetail(
                step_name=w.step_name,
                message=w.message,
            )
            for w in result.warnings
        ],
    )

@router.post(
    "/plan",
    summary="Generate dry-run execution plan",
    description="Generate an execution plan without actually running the pipeline.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def plan_pipeline(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Generate a dry-run execution plan for a pipeline."""
    plan = generate_execution_plan(body.yaml_config, db)

    return {
        "pipeline_name": plan.pipeline_name,
        "total_steps": plan.total_steps,
        "estimated_total_duration_ms": plan.estimated_total_duration_ms,
        "files_read": plan.files_read,
        "files_written": plan.files_written,
        "estimated_rows_processed": plan.estimated_rows_processed,
        "will_succeed": plan.will_succeed,
        "warnings": plan.warnings,
        "steps": [
            {
                "step_index": s.step_index,
                "step_name": s.step_name,
                "step_type": s.step_type,
                "input_step": s.input_step,
                "input_file_id": s.input_file_id,
                "estimated_rows_in": s.estimated_rows_in,
                "estimated_rows_out": s.estimated_rows_out,
                "estimated_columns": s.estimated_columns,
                "estimated_duration_ms": s.estimated_duration_ms,
                "warnings": s.warnings,
                "will_fail": s.will_fail,
                "fail_reason": s.fail_reason,
            }
            for s in plan.steps
        ],
    }

@router.post(
    "/run",
    response_model=RunPipelineResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a pipeline run",
    description="Queue a pipeline for asynchronous execution. Returns immediately.",
)
@limiter.limit(settings.RATE_LIMIT_PIPELINE_RUN)
def run_pipeline(
    request: Request,
    body: RunPipelineRequest,
    response: Response,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> RunPipelineResponse:
    """Create a pipeline run record and queue it for execution."""
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)
    pipeline_name = body.name or config.name

    pipeline_run = PipelineRun(
        name=pipeline_name,
        status=PipelineStatus.PENDING,
        yaml_config=body.yaml_config,
        user_id=current_user.id if current_user else None,
    )
    db.add(pipeline_run)
    db.commit()
    db.refresh(pipeline_run)

    execute_pipeline_task.delay(str(pipeline_run.id))

    from backend.services.audit_service import log_action
    log_action(db, "pipeline_run", user_id=current_user.id if current_user else None,
               resource_type="pipeline", resource_id=pipeline_run.id,
               details={"name": pipeline_name}, request=request)

    logger.info(
        "Pipeline run queued: id=%s, name=%s",
        pipeline_run.id, pipeline_name,
    )

    return RunPipelineResponse(
        run_id=str(pipeline_run.id),
        status=pipeline_run.status.value,
    )

@router.get(
    "/",
    response_model=PipelineRunListResponse,
    summary="List pipeline runs",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def list_pipeline_runs(
    request: Request,
    response: Response,
    db: Session = get_db_dependency(),
) -> PipelineRunListResponse:
    """List all pipeline runs ordered by creation time."""
    runs = (
        db.query(PipelineRun)
        .order_by(PipelineRun.created_at.desc())
        .all()
    )
    return PipelineRunListResponse(
        runs=[_run_to_response(r) for r in runs],
        total=len(runs),
    )

@router.get(
    "/stats",
    summary="Get pipeline statistics",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_pipeline_stats(
    request: Request,
    response: Response,
    db: Session = get_db_dependency(),
):
    """Get aggregate pipeline statistics."""
    from sqlalchemy import func
    total_runs = db.query(func.count(PipelineRun.id)).scalar() or 0
    completed = db.query(func.count(PipelineRun.id)).filter(
        PipelineRun.status == PipelineStatus.COMPLETED
    ).scalar() or 0
    failed = db.query(func.count(PipelineRun.id)).filter(
        PipelineRun.status == PipelineStatus.FAILED
    ).scalar() or 0
    pending = db.query(func.count(PipelineRun.id)).filter(
        PipelineRun.status == PipelineStatus.PENDING
    ).scalar() or 0
    total_files = db.query(func.count(UploadedFile.id)).scalar() or 0
    return {
        "total_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "success_rate": round(completed / total_runs * 100, 1) if total_runs > 0 else 0,
        "total_files": total_files,
    }

@router.get(
    "/{run_id}",
    response_model=PipelineRunResponse,
    summary="Get pipeline run details",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_pipeline_run(
    run_id: str,
    request: Request,
    response: Response,
    db: Session = get_db_dependency(),
) -> PipelineRunResponse:
    """Get full details of a specific pipeline run."""
    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )
    return _run_to_response(pipeline_run)

@router.get(
    "/{run_id}/stream",
    summary="Stream pipeline progress via SSE",
    description="Server-Sent Events stream for real-time pipeline progress.",
)
async def stream_pipeline_progress(
    run_id: str,
    db: Session = get_db_dependency(),
) -> StreamingResponse:
    """Stream real-time pipeline execution progress via Server-Sent Events."""
    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    # If already completed/failed, send a single terminal event
    if pipeline_run.status in (PipelineStatus.COMPLETED, PipelineStatus.FAILED):
        return StreamingResponse(
            _completed_event_generator(pipeline_run),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    return StreamingResponse(
        _live_event_generator(run_id),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )

async def _live_event_generator(run_id: str) -> AsyncGenerator[str, None]:
    """Subscribe to Redis and yield SSE events until pipeline completes."""
    redis_client = aioredis.from_url(settings.REDIS_URL)
    pubsub = redis_client.pubsub()
    channel = f"pipeline_progress:{run_id}"

    try:
        await pubsub.subscribe(channel)
        logger.info("SSE stream connected: run_id=%s", run_id)

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=1.0
            )
            if message is None:
                # Send keepalive comment to prevent proxy timeouts
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)
                continue

            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            if not isinstance(data, str):
                continue

            event_type = _extract_event_type(data)
            yield f"event: {event_type}\ndata: {data}\n\n"

            if event_type in _TERMINAL_EVENT_TYPES:
                logger.info("SSE stream closing: run_id=%s, event=%s", run_id, event_type)
                break

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled by client: run_id=%s", run_id)
    finally:
        await pubsub.unsubscribe(channel)
        await redis_client.aclose()

async def _completed_event_generator(
    pipeline_run: PipelineRun,
) -> AsyncGenerator[str, None]:
    """Yield a single terminal event for an already-completed pipeline."""
    event_type = (
        "pipeline_completed"
        if pipeline_run.status == PipelineStatus.COMPLETED
        else "pipeline_failed"
    )
    data = json.dumps({
        "run_id": str(pipeline_run.id),
        "event_type": event_type,
        "status": pipeline_run.status.value,
    })
    yield f"event: {event_type}\ndata: {data}\n\n"

def _extract_event_type(data: str) -> str:
    """Extract the event type from a JSON message."""
    try:
        parsed = json.loads(data)
        event_type = parsed.get("event_type", "")
        if event_type:
            return event_type
        step_status = parsed.get("status", "")
        status_to_event = {
            "RUNNING": "step_started",
            "COMPLETED": "step_completed",
            "FAILED": "step_failed",
        }
        return status_to_event.get(step_status, "progress")
    except (json.JSONDecodeError, AttributeError):
        return "progress"

def _sse_headers() -> dict:
    """Standard headers for SSE responses."""
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }

def _validate_uuid_format(value: str) -> None:
    """Raise 422 if the value is not a valid UUID."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID format: '{value}'",
        )

def _ensure_utc(dt):
    """Attach UTC tzinfo to naive datetimes from SQLite."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

def _run_to_response(pipeline_run: PipelineRun) -> PipelineRunResponse:
    """Convert a PipelineRun ORM model to an API response."""
    return PipelineRunResponse(
        id=str(pipeline_run.id),
        name=pipeline_run.name,
        status=pipeline_run.status.value,
        created_at=_ensure_utc(pipeline_run.created_at),
        started_at=_ensure_utc(pipeline_run.started_at),
        completed_at=_ensure_utc(pipeline_run.completed_at),
        duration_ms=pipeline_run.duration_ms,
        total_rows_in=pipeline_run.total_rows_in,
        total_rows_out=pipeline_run.total_rows_out,
        error_message=pipeline_run.error_message,
        step_results=[
            StepResultResponse(
                step_name=sr.step_name,
                step_type=sr.step_type,
                step_index=sr.step_index,
                status=sr.status.value,
                rows_in=sr.rows_in,
                rows_out=sr.rows_out,
                columns_in=sr.columns_in,
                columns_out=sr.columns_out,
                duration_ms=sr.duration_ms,
                warnings=sr.warnings,
                error_message=sr.error_message,
            )
            for sr in pipeline_run.step_results
        ],
    )
