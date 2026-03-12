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
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import validate_uuid_format as _validate_uuid_format, as_uuid as _as_uuid

logger = logging.getLogger(__name__)

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

    registered_ids = {str(row[0]) for row in db.query(UploadedFile.id).all()}
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
    page: int = 1,
    limit: int = 20,
    status_filter: str = None,
    db: Session = get_db_dependency(),
) -> PipelineRunListResponse:
    """List pipeline runs with pagination, ordered by creation time."""
    page = max(1, page)
    limit = max(1, min(limit, 100))

    query = db.query(PipelineRun)
    if status_filter:
        try:
            ps = PipelineStatus(status_filter.upper())
            query = query.filter(PipelineRun.status == ps)
        except ValueError:
            pass

    total = query.count()
    runs = (
        query
        .order_by(PipelineRun.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return PipelineRunListResponse(
        runs=[_run_to_response(r) for r in runs],
        total=total,
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


@router.post(
    "/{run_id}/cancel",
    summary="Cancel a running pipeline",
    description="Cancel a PENDING or RUNNING pipeline run and revoke its Celery task.",
)
def cancel_pipeline_run(
    run_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel a pipeline run by setting status to CANCELLED and revoking the task."""
    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    if pipeline_run.status not in (PipelineStatus.PENDING, PipelineStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel pipeline with status '{pipeline_run.status.value}'",
        )

    # Revoke the Celery task
    from backend.celery_app import celery_app as _celery_app
    _celery_app.control.revoke(run_id, terminate=True, signal="SIGTERM")

    from backend.utils.time_utils import utcnow
    pipeline_run.status = PipelineStatus.CANCELLED
    pipeline_run.completed_at = utcnow()
    pipeline_run.error_message = "Cancelled by user"
    db.commit()

    log_action(db, "pipeline_cancelled", user_id=current_user.id,
               resource_type="pipeline", resource_id=pipeline_run.id,
               request=request)

    logger.info("Pipeline run cancelled: id=%s", run_id)
    return {"run_id": run_id, "status": "CANCELLED"}


@router.post(
    "/preview",
    summary="Preview sample data at a pipeline step",
    description="Parse and dry-run pipeline up to a step, returning first 5 rows.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def preview_pipeline_step(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    step_index: int = 0,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Preview sample data at a specific step of a pipeline."""
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)

    if step_index < 0 or step_index >= len(config.steps):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"step_index {step_index} out of range (0..{len(config.steps) - 1})",
        )

    plan = generate_execution_plan(body.yaml_config, db)

    step_info = None
    if step_index < len(plan.steps):
        s = plan.steps[step_index]
        step_info = {
            "step_name": s.step_name,
            "step_type": s.step_type,
            "estimated_rows_in": s.estimated_rows_in,
            "estimated_rows_out": s.estimated_rows_out,
            "estimated_columns": s.estimated_columns,
        }

    return {
        "pipeline_name": config.name,
        "step_index": step_index,
        "total_steps": len(config.steps),
        "step_preview": step_info,
        "note": "Full sample data preview requires pipeline execution. Use /plan for detailed estimates.",
    }


@router.get(
    "/{run_id}/export",
    summary="Export pipeline run output",
    description="Download the output file from a completed pipeline run.",
)
def export_pipeline_output(
    run_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Export/download the output file from a completed pipeline run."""
    from pathlib import Path
    from fastapi.responses import FileResponse

    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    if pipeline_run.status != PipelineStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pipeline run is not completed (status: {pipeline_run.status.value})",
        )

    # Look for output files in the uploads directory matching this run
    output_dir = settings.UPLOAD_DIR
    possible_patterns = [
        output_dir / f"{run_id}*.csv",
        output_dir / f"{run_id}*.json",
    ]

    import glob as glob_module
    output_files = []
    for pattern in possible_patterns:
        output_files.extend(glob_module.glob(str(pattern)))

    # Also check for files saved by the pipeline (look in step results for save steps)
    for sr in pipeline_run.step_results:
        if sr.step_type == "save":
            # Try common output paths
            for ext in [".csv", ".json"]:
                candidate = output_dir / f"{sr.step_name}{ext}"
                if candidate.exists():
                    output_files.append(str(candidate))

    if not output_files:
        # Fall back: check if any output filename is embedded in the YAML
        import yaml
        try:
            parsed = yaml.safe_load(pipeline_run.yaml_config)
            steps = parsed.get("pipeline", {}).get("steps", [])
            for step in steps:
                if step.get("type") == "save":
                    filename = step.get("filename", "")
                    if filename:
                        candidate = output_dir / filename
                        if candidate.exists():
                            output_files.append(str(candidate))
        except Exception:
            pass

    if not output_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No output file found for this pipeline run",
        )

    output_path = Path(output_files[0])
    media_type = "text/csv" if output_path.suffix == ".csv" else "application/json"

    return FileResponse(
        path=str(output_path),
        media_type=media_type,
        filename=output_path.name,
    )
