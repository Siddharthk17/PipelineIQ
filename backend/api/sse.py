"""Dedicated SSE endpoints for pipeline progress streaming."""

import asyncio
import logging
import time
from typing import AsyncGenerator, Optional

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.auth import get_current_user_sse
from backend.db.redis_pools import get_cache_redis_async, get_pubsub_redis_async
from backend.dependencies import get_read_db_dependency
from backend.models import PipelineRun, PipelineStatus, User
from backend.utils.uuid_utils import as_uuid as _as_uuid, validate_uuid_format as _validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
legacy_router = APIRouter(prefix="/runs", tags=["runs"])

HEARTBEAT_INTERVAL_SECONDS = 15
_STATUS_CACHE_TTL_SECONDS = 3600
_TERMINAL_STATUSES = frozenset({
    PipelineStatus.HEALED.value,
    PipelineStatus.COMPLETED.value,
    PipelineStatus.FAILED.value,
    PipelineStatus.CANCELLED.value,
    PipelineStatus.TIMEOUT.value,
})
_TERMINAL_EVENT_TYPES = frozenset({
    "pipeline_completed",
    "pipeline_failed",
    "pipeline_cancelled",
    "stream_end",
})

@router.get(
    "/{run_id}/stream",
    summary="Stream pipeline progress via SSE",
    description="Server-Sent Events stream for real-time pipeline progress.",
)
async def stream_pipeline_progress(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user_sse),
) -> StreamingResponse:
    """Stream real-time pipeline execution progress via Server-Sent Events."""
    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _authorize_run_access(pipeline_run, current_user)

    if pipeline_run.status.value in _TERMINAL_STATUSES:
        return StreamingResponse(
            _completed_event_generator(pipeline_run),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    return StreamingResponse(
        _live_event_generator(run_id, request, pipeline_run.status.value),
        media_type="text/event-stream",
        headers=_sse_headers(),
    )


@legacy_router.get(
    "/{run_id}/stream",
    summary="Stream pipeline progress via SSE (legacy)",
    description="Legacy stream endpoint for /api/runs/{run_id}/stream compatibility.",
)
async def stream_pipeline_progress_legacy(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user_sse),
) -> StreamingResponse:
    """Legacy compatibility alias for stream endpoint."""
    return await stream_pipeline_progress(
        run_id=run_id,
        request=request,
        db=db,
        current_user=current_user,
    )

def _authorize_run_access(pipeline_run: PipelineRun, current_user: User) -> None:
    """Raise 403 if the user cannot access this pipeline run stream."""
    if current_user.role == "admin":
        return

    if pipeline_run.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this pipeline stream",
        )

    if str(pipeline_run.user_id) != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this pipeline stream",
        )

async def _live_event_generator(
    run_id: str,
    request: Request,
    initial_status: str,
) -> AsyncGenerator[str, None]:
    """Subscribe to Redis and yield SSE events until pipeline completes."""
    redis_client = get_pubsub_redis_async()
    cache_client = get_cache_redis_async()
    pubsub = redis_client.pubsub()
    channel = f"pipeline_progress:{run_id}"
    last_emit_at = time.monotonic()

    try:
        await pubsub.subscribe(channel)
        logger.info("SSE stream connected: run_id=%s", run_id)

        cached_event = await _get_cached_event(cache_client, run_id)
        if cached_event is not None:
            cached_event_type = _extract_event_type(cached_event)
            yield _format_sse_event(cached_event_type, cached_event)
            if _is_terminal_event(cached_event_type, cached_event):
                logger.info("SSE stream immediate close from cached terminal state: run_id=%s", run_id)
                yield _format_sse_event(
                    "stream_end",
                    {"run_id": run_id, "event_type": "stream_end", "status": cached_event.get("status")},
                )
                return
        else:
            yield _format_sse_event(
                "pipeline_status",
                {
                    "run_id": run_id,
                    "event_type": "pipeline_status",
                    "status": initial_status,
                },
            )

        while True:
            if await request.is_disconnected():
                logger.info("SSE stream disconnected by client: run_id=%s", run_id)
                return

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            now = time.monotonic()
            if message is None:
                if now - last_emit_at >= HEARTBEAT_INTERVAL_SECONDS:
                    yield ": heartbeat\n\n"
                    last_emit_at = now
                continue

            payload = _parse_message_payload(message.get("data"))
            if payload is None:
                continue

            event_type = _extract_event_type(payload)
            yield _format_sse_event(event_type, payload)
            last_emit_at = now

            if _is_terminal_event(event_type, payload):
                logger.info("SSE stream closing: run_id=%s, event=%s", run_id, event_type)
                yield _format_sse_event(
                    "stream_end",
                    {"run_id": run_id, "event_type": "stream_end", "status": payload.get("status")},
                )
                return

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled by client: run_id=%s", run_id)
        return
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await redis_client.aclose()
        await cache_client.aclose()

async def _completed_event_generator(
    pipeline_run: PipelineRun,
) -> AsyncGenerator[str, None]:
    """Yield terminal events for an already-completed pipeline."""
    event_type = _terminal_event_type(pipeline_run.status)
    payload = {
        "run_id": str(pipeline_run.id),
        "event_type": event_type,
        "status": pipeline_run.status.value,
        "error_message": pipeline_run.error_message,
    }
    yield _format_sse_event(event_type, payload)
    yield _format_sse_event(
        "stream_end",
        {"run_id": str(pipeline_run.id), "event_type": "stream_end", "status": pipeline_run.status.value},
    )

def _status_cache_key(run_id: str) -> str:
    return f"pipeline_progress:last:{run_id}"

async def _get_cached_event(cache_client, run_id: str) -> Optional[dict]:
    """Return the latest cached progress payload for this run, if present."""
    try:
        cached_payload = await cache_client.get(_status_cache_key(run_id))
    except RedisError:
        logger.warning("SSE cache read failed for run_id=%s", run_id)
        return None

    if cached_payload is None:
        return None

    try:
        parsed = orjson.loads(cached_payload)
    except orjson.JSONDecodeError:
        logger.warning("SSE cache payload is not valid JSON for run_id=%s", run_id)
        return None

    return parsed if isinstance(parsed, dict) else None

def _parse_message_payload(data) -> Optional[dict]:
    """Parse Redis pub/sub message payload into a dict."""
    if isinstance(data, dict):
        return data
    if not isinstance(data, (bytes, str, bytearray)):
        return None
    try:
        parsed = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

def _extract_event_type(payload: dict) -> str:
    """Extract the SSE event type from a progress payload."""
    event_type = payload.get("event_type")
    if isinstance(event_type, str) and event_type:
        return event_type

    step_status = payload.get("status")
    status_to_event = {
        "HEALED": "pipeline_completed",
        "RUNNING": "step_started",
        "COMPLETED": "step_completed",
        "FAILED": "step_failed",
        "CANCELLED": "pipeline_cancelled",
        "TIMEOUT": "pipeline_failed",
    }
    if isinstance(step_status, str):
        return status_to_event.get(step_status, "progress")
    return "progress"

def _is_terminal_event(event_type: str, payload: dict) -> bool:
    """Return True when no more events should be emitted for this run."""
    if event_type in _TERMINAL_EVENT_TYPES:
        return True
    status_value = payload.get("status")
    return isinstance(status_value, str) and status_value in _TERMINAL_STATUSES

def _terminal_event_type(status_value: PipelineStatus) -> str:
    if status_value in {PipelineStatus.COMPLETED, PipelineStatus.HEALED}:
        return "pipeline_completed"
    if status_value == PipelineStatus.CANCELLED:
        return "pipeline_cancelled"
    return "pipeline_failed"

def _format_sse_event(event_type: str, payload: dict) -> str:
    """Format an SSE message with event and data lines."""
    serialized = orjson.dumps(payload).decode("utf-8")
    return f"event: {event_type}\ndata: {serialized}\n\n"

def _sse_headers() -> dict:
    """Standard headers for SSE responses."""
    return {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
