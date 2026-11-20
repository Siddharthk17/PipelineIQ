"""Dedicated SSE endpoints for pipeline progress streaming."""

import asyncio
import contextlib
import logging
import os
import random
import time
from collections.abc import AsyncGenerator, Awaitable, Callable

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.db.redis_pools import get_cache_redis_async, get_pubsub_redis_async
from backend.dependencies import get_read_db_dependency
from backend.models import PipelineRun, PipelineStatus, User
from backend.utils.sse_security import public_sse_payload, verify_sse_payload
from backend.utils.uuid_utils import as_uuid as _as_uuid
from backend.utils.uuid_utils import validate_uuid_format as _validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
legacy_router = APIRouter(prefix="/runs", tags=["runs"])

HEARTBEAT_INTERVAL_SECONDS = 15
_STATUS_CACHE_TTL_SECONDS = 3600


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# M-106: per-user / global caps so one user cannot exhaust the shared
# Redis pub/sub pool or pin every read-DB connection with idle streams.
SSE_MAX_STREAMS_PER_USER = _env_int("SSE_MAX_STREAMS_PER_USER", 5)
SSE_MAX_STREAMS_TOTAL = _env_int("SSE_MAX_STREAMS_TOTAL", 200)
# M-171: bounded Redis retries — base delay with jitter, max attempts,
# plus a circuit breaker so a down Redis does not become a retry storm.
SSE_REDIS_MAX_RETRIES = _env_int("SSE_REDIS_MAX_RETRIES", 3)
SSE_REDIS_BASE_DELAY_SECONDS = _env_float("SSE_REDIS_BASE_DELAY_SECONDS", 0.1)
SSE_REDIS_OP_TIMEOUT_SECONDS = _env_float("SSE_REDIS_OP_TIMEOUT_SECONDS", 5.0)
SSE_CIRCUIT_THRESHOLD = _env_int("SSE_CIRCUIT_THRESHOLD", 5)
SSE_CIRCUIT_COOLDOWN_SECONDS = _env_float("SSE_CIRCUIT_COOLDOWN_SECONDS", 30.0)
# M-106 backpressure: coalesce bursts of non-terminal progress events.
# At most this many queued messages are collapsed into the latest state;
# terminal events are never dropped.
SSE_MAX_DRAIN_PER_TICK = _env_int("SSE_MAX_DRAIN_PER_TICK", 50)

_ACTIVE_STREAMS: dict[str, int] = {}
_ACTIVE_STREAMS_TOTAL = 0
_STREAMS_LOCK = asyncio.Lock()

_SSE_CIRCUIT = {"failures": 0, "open_until": 0.0}
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


async def _acquire_stream_slot(user_key: str) -> None:
    """Reserve a stream slot for this user (M-106 per-user cap).

    Raises 429 when the user or the service is at capacity, so excess
    load is rejected fast instead of exhausting the shared pools.
    The slot is released by the event generator's ``finally`` block.
    """
    global _ACTIVE_STREAMS_TOTAL
    async with _STREAMS_LOCK:
        per_user = _ACTIVE_STREAMS.get(user_key, 0)
        if per_user >= SSE_MAX_STREAMS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Too many concurrent streams "
                    f"({per_user}/{SSE_MAX_STREAMS_PER_USER}). "
                    "Close an idle stream and retry."
                ),
            )
        if _ACTIVE_STREAMS_TOTAL >= SSE_MAX_STREAMS_TOTAL:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="SSE service at capacity. Retry shortly.",
            )
        _ACTIVE_STREAMS[user_key] = per_user + 1
        _ACTIVE_STREAMS_TOTAL += 1


async def _release_stream_slot(user_key: str) -> None:
    """Release a previously acquired stream slot."""
    global _ACTIVE_STREAMS_TOTAL
    async with _STREAMS_LOCK:
        remaining = _ACTIVE_STREAMS.get(user_key, 0) - 1
        if remaining <= 0:
            _ACTIVE_STREAMS.pop(user_key, None)
        else:
            _ACTIVE_STREAMS[user_key] = remaining
        _ACTIVE_STREAMS_TOTAL = max(0, _ACTIVE_STREAMS_TOTAL - 1)


def _circuit_open() -> bool:
    return time.monotonic() < _SSE_CIRCUIT["open_until"]


def _record_circuit_success() -> None:
    _SSE_CIRCUIT["failures"] = 0


def _record_circuit_failure() -> None:
    _SSE_CIRCUIT["failures"] += 1
    if _SSE_CIRCUIT["failures"] >= SSE_CIRCUIT_THRESHOLD:
        _SSE_CIRCUIT["open_until"] = (
            time.monotonic() + SSE_CIRCUIT_COOLDOWN_SECONDS
        )
        logger.warning(
            "SSE redis circuit open after %d failures; cooling down %.0fs",
            _SSE_CIRCUIT["failures"],
            SSE_CIRCUIT_COOLDOWN_SECONDS,
        )


def _retry_delay(attempt: int) -> float:
    """Bounded exponential backoff with jitter (M-171)."""
    return SSE_REDIS_BASE_DELAY_SECONDS * (2 ** attempt) * (
        0.5 + random.random()
    )


async def _redis_op_with_retry(
    op_name: str,
    coro_factory: Callable[[], Awaitable],
):
    """Run a Redis op with bounded retries + circuit breaker (M-171).

    At most ``SSE_REDIS_MAX_RETRIES`` attempts with backoff+jitter;
    consecutive failures trip a cooldown circuit so a down Redis does
    not become a retry storm.
    """
    if _circuit_open():
        raise RedisError(f"SSE redis circuit open; skipping {op_name}")
    last_exc: Exception | None = None
    for attempt in range(max(1, SSE_REDIS_MAX_RETRIES)):
        try:
            result = await asyncio.wait_for(
                coro_factory(), timeout=SSE_REDIS_OP_TIMEOUT_SECONDS
            )
        except (TimeoutError, RedisError) as exc:
            last_exc = exc
            _record_circuit_failure()
            if attempt + 1 < max(1, SSE_REDIS_MAX_RETRIES):
                await asyncio.sleep(_retry_delay(attempt))
        else:
            _record_circuit_success()
            return result
    assert last_exc is not None
    raise last_exc


async def _require_redis_available(run_id: str) -> None:
    """Pre-flight ping of the pub/sub + cache pools (M-170).

    Raises 503 (honest degraded signal) when a dependency is down
    instead of opening a 200 stream that can never emit.
    """
    try:
        pubsub_client = get_pubsub_redis_async()
        cache_client = get_cache_redis_async()
        try:
            await _redis_op_with_retry("ping", pubsub_client.ping)
            await _redis_op_with_retry("ping", cache_client.ping)
        finally:
            with contextlib.suppress(Exception):
                await pubsub_client.aclose()
            with contextlib.suppress(Exception):
                await cache_client.aclose()
    except (TimeoutError, RedisError) as exc:
        logger.warning(
            "SSE stream refused (degraded): redis unavailable for run_id=%s: %s",
            run_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Streaming backend temporarily unavailable. Retry shortly.",
        ) from exc


@router.get(
    "/{run_id}/stream",
    summary="Stream pipeline progress via SSE",
    description="Server-Sent Events stream for real-time pipeline progress.",
)
async def stream_pipeline_progress(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    # Header (Bearer) or HttpOnly cookie only. ?token= query auth was
    # removed (M-068): URLs persist in browser history, proxies, and
    # logs, while EventSource clients send cookies automatically.
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream real-time pipeline execution progress via Server-Sent Events."""
    _validate_uuid_format(run_id)
    pipeline_run = db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first()
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _authorize_run_access(pipeline_run, current_user, db)

    # M-106: copy everything the stream needs out of the ORM object, then
    # release the read-DB session BEFORE the long-lived stream starts.
    # Holding a pooled session (and its connection) for the whole stream
    # lifetime exhausts the shared pool under fan-out.
    status_value = pipeline_run.status.value
    error_message = pipeline_run.error_message
    run_id_str = str(pipeline_run.id)
    db.close()

    user_key = str(current_user.id)
    await _acquire_stream_slot(user_key)

    if status_value in _TERMINAL_STATUSES:
        return StreamingResponse(
            _completed_event_generator(
                run_id_str, status_value, error_message, user_key),
            media_type="text/event-stream",
            headers=_sse_headers(),
        )

    # M-170: pre-flight the Redis pub/sub + cache pools before committing
    # to a 200 stream. If a dependency is down the honest answer is 503
    # here — not a 200 that silently yields nothing.
    await _require_redis_available(run_id_str)

    return StreamingResponse(
        _live_event_generator(run_id_str, request, status_value, user_key),
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
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Legacy compatibility alias for stream endpoint."""
    return await stream_pipeline_progress(
        run_id=run_id,
        request=request,
        db=db,
        current_user=current_user,
    )


def _authorize_run_access(
        pipeline_run: PipelineRun,
        current_user: User,
        db: Session | None = None) -> None:
    """Raise 403 if the user cannot access this pipeline run stream.

    Mirrors the REST run gates: admins, the run owner, and holders of a
    per-pipeline grant (owner/runner/viewer) may watch the stream.
    """
    if current_user.role == "admin":
        return

    if pipeline_run.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this pipeline stream",
        )

    if str(pipeline_run.user_id) == str(current_user.id):
        return

    if db is not None:
        from backend.models import PermissionLevel, PipelinePermission

        grant = (
            db.query(PipelinePermission)
            .filter(
                PipelinePermission.pipeline_name == pipeline_run.name,
                PipelinePermission.user_id == current_user.id,
            )
            .first()
        )
        if grant and grant.permission_level in (
            PermissionLevel.OWNER,
            PermissionLevel.RUNNER,
            PermissionLevel.VIEWER,
        ):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to access this pipeline stream",
    )


async def _live_event_generator(
    run_id: str,
    request: Request,
    initial_status: str,
    user_key: str = "",
) -> AsyncGenerator[str, None]:
    """Subscribe to Redis and yield SSE events until pipeline completes.

    Holds no DB session (released by the endpoint before streaming).
    Applies the documented backpressure policy: bursts of non-terminal
    progress events are coalesced to the latest state; terminal events
    are never dropped. Redis read failures use bounded retries with
    backoff+jitter; a sustained outage ends the stream so the client
    reconnects with its own backoff instead of spinning (M-106/M-171).
    """
    redis_client = get_pubsub_redis_async()
    cache_client = get_cache_redis_async()
    pubsub = redis_client.pubsub()
    channel = f"pipeline_progress:{run_id}"
    last_emit_at = time.monotonic()
    consecutive_redis_errors = 0

    try:
        try:
            await _redis_op_with_retry(
                "subscribe", lambda: pubsub.subscribe(channel))
        except (TimeoutError, RedisError) as exc:
            logger.warning(
                "SSE stream degraded (no subscribe) for run_id=%s: %s",
                run_id, exc)
            return
        logger.info("SSE stream connected: run_id=%s", run_id)

        cached_event = await _get_cached_event(cache_client, run_id)
        if cached_event is not None:
            cached_event_type = _extract_event_type(cached_event)
            yield _format_sse_event(cached_event_type, cached_event)
            if _is_terminal_event(cached_event_type, cached_event):
                logger.info(
                    "SSE stream immediate close from cached terminal state: run_id=%s",
                    run_id)
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
                logger.info(
                    "SSE stream disconnected by client: run_id=%s", run_id)
                return

            try:
                message = await asyncio.wait_for(
                    pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0),
                    timeout=SSE_REDIS_OP_TIMEOUT_SECONDS + 1.0,
                )
            except (TimeoutError, RedisError) as exc:
                consecutive_redis_errors += 1
                _record_circuit_failure()
                logger.warning(
                    "SSE redis read failed (%d/%d) for run_id=%s: %s",
                    consecutive_redis_errors,
                    SSE_REDIS_MAX_RETRIES,
                    run_id,
                    exc,
                )
                if consecutive_redis_errors >= max(1, SSE_REDIS_MAX_RETRIES):
                    logger.warning(
                        "SSE stream degraded-close for run_id=%s "
                        "after %d redis failures",
                        run_id, consecutive_redis_errors)
                    return
                await asyncio.sleep(
                    _retry_delay(consecutive_redis_errors))
                continue
            consecutive_redis_errors = 0
            now = time.monotonic()
            if message is None:
                if now - last_emit_at >= HEARTBEAT_INTERVAL_SECONDS:
                    yield ": heartbeat\n\n"
                    last_emit_at = now
                continue

            payload = _parse_message_payload(message.get("data"))
            if payload is None:
                continue

            # M-106 backpressure: coalesce bursts — drain what is already
            # buffered and keep only the latest non-terminal payload.
            # Terminal payloads short-circuit the drain (never dropped).
            dropped = 0
            event_type = _extract_event_type(payload)
            if not _is_terminal_event(event_type, payload):
                for _ in range(SSE_MAX_DRAIN_PER_TICK):
                    try:
                        pending = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=0)
                    except (TimeoutError, RedisError):
                        break
                    if pending is None:
                        break
                    pending_payload = _parse_message_payload(
                        pending.get("data"))
                    if pending_payload is None:
                        continue
                    pending_type = _extract_event_type(pending_payload)
                    if _is_terminal_event(pending_type, pending_payload):
                        payload = pending_payload
                        event_type = pending_type
                        break
                    payload = pending_payload
                    event_type = pending_type
                    dropped += 1
                if dropped:
                    logger.debug(
                        "SSE backpressure: coalesced %d events for run_id=%s",
                        dropped, run_id)
            yield _format_sse_event(event_type, payload)
            last_emit_at = now

            if _is_terminal_event(event_type, payload):
                logger.info(
                    "SSE stream closing: run_id=%s, event=%s",
                    run_id,
                    event_type)
                yield _format_sse_event(
                    "stream_end",
                    {"run_id": run_id, "event_type": "stream_end", "status": payload.get("status")},
                )
                return

    except asyncio.CancelledError:
        logger.info("SSE stream cancelled by client: run_id=%s", run_id)
        return
    finally:
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
        with contextlib.suppress(Exception):
            await pubsub.aclose()
        with contextlib.suppress(Exception):
            await redis_client.aclose()
        with contextlib.suppress(Exception):
            await cache_client.aclose()
        if user_key:
            await _release_stream_slot(user_key)


async def _completed_event_generator(
    run_id: str,
    status_value: str,
    error_message: str | None,
    user_key: str = "",
) -> AsyncGenerator[str, None]:
    """Yield terminal events for an already-completed pipeline."""
    try:
        try:
            status_enum = PipelineStatus(status_value)
        except ValueError:
            status_enum = PipelineStatus.FAILED
        event_type = _terminal_event_type(status_enum)
        payload = {
            "run_id": run_id,
            "event_type": event_type,
            "status": status_value,
            "error_message": error_message,
        }
        yield _format_sse_event(event_type, payload)
        yield _format_sse_event(
            "stream_end",
            {"run_id": run_id, "event_type": "stream_end", "status": status_value},
        )
    finally:
        if user_key:
            await _release_stream_slot(user_key)


def _status_cache_key(run_id: str) -> str:
    return f"pipeline_progress:last:{run_id}"


async def _get_cached_event(cache_client, run_id: str) -> dict | None:
    """Return the latest cached progress payload for this run, if present.

    Degraded-not-lying (M-170): a cache outage logs a warning and yields
    ``None`` so the live stream still serves fresh events; the outage is
    surfaced via ``/readyz`` (503), not hidden inside a 200 stream.
    Reads use bounded retries with backoff+jitter (M-171).
    """
    try:
        cached_payload = await _redis_op_with_retry(
            "cache-get", lambda: cache_client.get(_status_cache_key(run_id))
        )
    except (TimeoutError, RedisError) as exc:
        _record_circuit_failure()
        logger.warning(
            "SSE degraded: cache read failed for run_id=%s "
            "(serving live stream without cached state): %s",
            run_id,
            exc,
        )
        return None

    if cached_payload is None:
        return None

    try:
        parsed = orjson.loads(cached_payload)
    except orjson.JSONDecodeError:
        logger.warning(
            "SSE cache payload is not valid JSON for run_id=%s",
            run_id)
        return None

    if not isinstance(parsed, dict):
        return None
    if not verify_sse_payload(parsed):
        logger.warning("SSE cache payload failed signature verification for run_id=%s", run_id)
        return None
    return parsed


def _parse_message_payload(data) -> dict | None:
    """Parse Redis pub/sub message payload into a dict."""
    if isinstance(data, dict):
        # M-156: in-memory dict payloads must verify like wire payloads —
        # no unsigned bypass regardless of type.
        if not verify_sse_payload(data):
            logger.warning("Dropped unsigned or invalid SSE Redis payload")
            return None
        return data
    if not isinstance(data, (bytes, str, bytearray)):
        return None
    try:
        parsed = orjson.loads(data)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if not verify_sse_payload(parsed):
        logger.warning("Dropped unsigned or invalid SSE Redis payload")
        return None
    return parsed


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
    serialized = orjson.dumps(public_sse_payload(payload)).decode("utf-8")
    return f"event: {event_type}\ndata: {serialized}\n\n"


def _sse_headers() -> dict:
    """Standard headers for SSE responses."""
    return {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
