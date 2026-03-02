"""Pipeline execution API endpoints with SSE streaming.

Provides endpoints for pipeline validation, execution (async via Celery),
result retrieval, and real-time progress streaming via Server-Sent Events.
"""

# Standard library
import asyncio
import json
import logging
from typing import AsyncGenerator

# Third-party packages
import redis.asyncio as aioredis
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

# Internal modules
from backend.config import settings
from backend.dependencies import get_db_dependency
from backend.models import PipelineRun, PipelineStatus, UploadedFile
from backend.pipeline.parser import PipelineParser
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
def validate_pipeline(
    request: ValidatePipelineRequest,
    db: Session = get_db_dependency(),
) -> ValidatePipelineResponse:
    """Validate a pipeline configuration against registered files.

    Args:
        request: Request containing the YAML configuration.
        db: Database session (injected).

    Returns:
        Validation result with errors and warnings.
    """
    parser = PipelineParser()
    config = parser.parse(request.yaml_config)

    registered_ids = {f.id for f in db.query(UploadedFile).all()}
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
    "/run",
    response_model=RunPipelineResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a pipeline run",
    description="Queue a pipeline for asynchronous execution. Returns immediately.",
)
def run_pipeline(
    request: RunPipelineRequest,
    db: Session = get_db_dependency(),
) -> RunPipelineResponse:
    """Create a pipeline run record and queue it for execution.

    The endpoint returns immediately with a run_id and PENDING status.
    The actual execution happens asynchronously via a Celery worker.

    Args:
        request: Request containing YAML config and optional name.
        db: Database session (injected).

    Returns:
        RunPipelineResponse with the run_id for tracking.
    """
    parser = PipelineParser()
    config = parser.parse(request.yaml_config)
    pipeline_name = request.name or config.name

    pipeline_run = PipelineRun(
        name=pipeline_name,
        status=PipelineStatus.PENDING,
        yaml_config=request.yaml_config,
    )
    db.add(pipeline_run)
    db.commit()
    db.refresh(pipeline_run)

    execute_pipeline_task.delay(pipeline_run.id)

    logger.info(
        "Pipeline run queued: id=%s, name=%s",
        pipeline_run.id, pipeline_name,
    )

    return RunPipelineResponse(
        run_id=pipeline_run.id,
        status=pipeline_run.status.value,
    )


@router.get(
    "/",
    response_model=PipelineRunListResponse,
    summary="List pipeline runs",
)
def list_pipeline_runs(
    db: Session = get_db_dependency(),
) -> PipelineRunListResponse:
    """List all pipeline runs ordered by creation time.

    Args:
        db: Database session (injected).

    Returns:
        PipelineRunListResponse with all runs.
    """
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
    "/{run_id}",
    response_model=PipelineRunResponse,
    summary="Get pipeline run details",
)
def get_pipeline_run(
    run_id: str,
    db: Session = get_db_dependency(),
) -> PipelineRunResponse:
    """Get full details of a specific pipeline run.

    Args:
        run_id: The pipeline run ID.
        db: Database session (injected).

    Returns:
        PipelineRunResponse with step results and timing.

    Raises:
        HTTPException 404: If the run_id is not found.
    """
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
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
    """Stream real-time pipeline execution progress via Server-Sent Events.

    Connects to Redis pub/sub channel f"pipeline_progress:{run_id}",
    forwards messages to the HTTP client as SSE events, and closes
    the connection when a terminal event is received.

    Args:
        run_id: The pipeline run ID to stream progress for.
        db: Database session (injected).

    Returns:
        StreamingResponse with text/event-stream content type.

    Raises:
        HTTPException 404: If the run_id is not found.
    """
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
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


# ═══════════════════════════════════════════════════════════════════════════════
# SSE EVENT GENERATORS
# ═══════════════════════════════════════════════════════════════════════════════


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
        "run_id": pipeline_run.id,
        "event_type": event_type,
        "status": pipeline_run.status.value,
    })
    yield f"event: {event_type}\ndata: {data}\n\n"


def _extract_event_type(data: str) -> str:
    """Extract the event type from a JSON message."""
    try:
        parsed = json.loads(data)
        # Check for terminal events
        event_type = parsed.get("event_type", "")
        if event_type:
            return event_type
        # Map step status to event type
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


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSE BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _run_to_response(pipeline_run: PipelineRun) -> PipelineRunResponse:
    """Convert a PipelineRun ORM model to an API response."""
    return PipelineRunResponse(
        id=pipeline_run.id,
        name=pipeline_run.name,
        status=pipeline_run.status.value,
        created_at=pipeline_run.created_at,
        started_at=pipeline_run.started_at,
        completed_at=pipeline_run.completed_at,
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
