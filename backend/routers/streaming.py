"""Streaming pipeline control and Dead Letter Queue management API."""

import asyncio
import contextlib
import logging
import time
import uuid as uuid_module

import orjson
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth import get_current_admin, get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PipelineRun, StreamingStats, User
from backend.streaming.access import base_topic, check_topic_access, get_accessible_topics
from backend.streaming.redpanda_client import (
    get_admin_client,
    make_consumer,
    make_producer,
    validate_topic_name,
)

router = APIRouter(prefix="/api/streaming", tags=["Streaming"])
logger = logging.getLogger(__name__)

# M-063: bounds for DLQ scans. The blocking consume loop runs in a worker
# thread with its own budget; the outer wait_for is slack on top of it.
DLQ_MAX_LIMIT = 500
DLQ_INSPECT_BUDGET_SEC = 5.0
DLQ_INSPECT_TIMEOUT_SEC = 12.0
DLQ_REPLAY_BUDGET_SEC = 30.0
DLQ_REPLAY_TIMEOUT_SEC = 45.0
# Consecutive empty polls before a DLQ scan is considered idle/drained.
DLQ_IDLE_POLLS = 3


def _clamp_limit(limit: int) -> int:
    """Clamp a DLQ scan limit into [0, DLQ_MAX_LIMIT] (M-063)."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, DLQ_MAX_LIMIT))


def _decode_dlq_message(msg) -> dict:
    """Decode one consumed DLQ record into its JSON-serializable form."""
    headers: dict[str, str] = {}
    raw_headers = msg.headers() or []
    if isinstance(raw_headers, list):
        for k, v in raw_headers:
            if isinstance(v, bytes):
                headers[str(k)] = v.decode("utf-8", errors="replace")
            elif v is not None:
                headers[str(k)] = str(v)
    try:
        raw_value = msg.value()
        if raw_value is None:
            val = None
        else:
            val = raw_value.decode("utf-8", errors="replace")
            with contextlib.suppress(Exception):
                val = orjson.loads(val)
    except Exception:
        val = "<binary>"
    raw_key = msg.key()
    return {
        "partition": msg.partition(),
        "offset": msg.offset(),
        "key": raw_key.decode("utf-8", errors="replace") if raw_key else None,
        "value": val,
        "error": headers.get("x-error"),
        "original_topic": headers.get("x-original-topic"),
        "failed_at": headers.get("x-failed-at"),
    }


def _consume_dlq_sync(
    dlq_topic: str,
    limit: int,
    *,
    budget_sec: float = DLQ_INSPECT_BUDGET_SEC,
    idle_polls_before_done: int = DLQ_IDLE_POLLS,
    poll_timeout: float = 1.0,
    max_batch: int = 50,
) -> list:
    """Blocking DLQ scan. Runs in a worker thread — never on the event loop.

    Reads until ``limit`` is reached, the budget expires, or the consumer
    goes idle (``idle_polls_before_done`` consecutive empty polls), so a
    transient empty poll no longer truncates the result (M-063).
    """
    group = f"dlq-inspector-{uuid_module.uuid4().hex[:8]}"
    consumer = make_consumer(group, auto_offset_reset="earliest")
    try:
        consumer.subscribe([dlq_topic])
        messages: list = []
        idle = 0
        deadline = time.monotonic() + budget_sec
        while len(messages) < limit and time.monotonic() < deadline:
            try:
                batch = consumer.consume(
                    num_messages=min(limit - len(messages), max_batch),
                    timeout=poll_timeout,
                )
            except Exception:
                logger.exception("DLQ consume failed for %s", dlq_topic)
                break
            if not batch:
                idle += 1
                if idle >= idle_polls_before_done:
                    break
                continue
            idle = 0
            for msg in batch:
                if msg.error():
                    continue
                messages.append(_decode_dlq_message(msg))
                if len(messages) >= limit:
                    break
        return messages
    finally:
        try:
            consumer.close()
        except Exception:
            logger.exception("DLQ consumer close failed for %s", dlq_topic)


def _replay_dlq_sync(
    topic: str,
    dlq_topic: str,
    limit: int,
    *,
    budget_sec: float = DLQ_REPLAY_BUDGET_SEC,
    idle_polls_before_done: int = DLQ_IDLE_POLLS,
) -> int:
    """Blocking DLQ replay. Runs in a worker thread (M-063).

    Honest accounting (R-R-007): only messages the broker acknowledges
    count as replayed. A failed flush raises instead of reporting a
    phantom count, and consumed offsets are committed so a repeated
    replay resumes instead of re-sending the same prefix.
    """
    import hashlib

    admin = get_admin_client()
    admin.ensure_topic(topic)
    # Stable group per DLQ topic so offsets persist across replays:
    # a repeated replay continues where the last one stopped instead
    # of duplicating the whole prefix.
    group = "dlq-replay-" + hashlib.sha256(
        dlq_topic.encode("utf-8")).hexdigest()[:16]
    consumer = make_consumer(group, auto_offset_reset="earliest")
    producer = make_producer()
    delivered = 0
    delivery_errors: list = []

    def _on_delivery(err, _msg):
        nonlocal delivered
        if err is None:
            delivered += 1
        else:
            delivery_errors.append(err)

    try:
        consumer.subscribe([dlq_topic])
        replayed = 0
        idle = 0
        deadline = time.monotonic() + budget_sec
        while replayed < limit and time.monotonic() < deadline:
            try:
                batch = consumer.consume(num_messages=min(limit - replayed, 100), timeout=2.0)
            except Exception:
                logger.exception("DLQ replay consume failed for %s", dlq_topic)
                break
            if not batch:
                idle += 1
                if idle >= idle_polls_before_done:
                    break
                continue
            idle = 0
            for msg in batch:
                if msg.error():
                    continue
                producer.produce(
                    topic=topic, value=msg.value(), key=msg.key(),
                    callback=_on_delivery,
                )
                replayed += 1
                if replayed >= limit:
                    break
            producer.poll(0)
        try:
            producer.flush(timeout=10)
        except Exception as exc:
            raise RuntimeError(
                f"DLQ replay flush failed after {delivered} delivered"
            ) from exc
        if delivery_errors:
            raise RuntimeError(
                f"DLQ replay lost {len(delivery_errors)} messages at produce time"
            )
        try:
            consumer.commit()
        except Exception:
            logger.warning("DLQ replay offset commit failed for %s", dlq_topic)
        return replayed
    finally:
        try:
            consumer.close()
        except Exception:
            logger.exception("DLQ replay consumer close failed for %s", dlq_topic)


def _call_transition(name: str, run_id: str, user: User):
    """Invoke a daemon-side transition function (M-078).

    The transition functions (``request_pause``/``request_resume``/
    ``request_stop``/``request_restart``) live in
    ``backend/tasks/streaming_pipeline.py`` (sibling-owned) and return
    ``(ok, error)``. Routes only do authz + map the result to HTTP codes —
    they never write run state themselves.
    """
    try:
        from backend.tasks import streaming_pipeline as daemon
    except ImportError as exc:
        raise HTTPException(503, "Streaming daemon unavailable") from exc
    fn = getattr(daemon, name, None)
    if fn is None:
        raise HTTPException(503, f"Streaming transition '{name}' unavailable")
    try:
        result = fn(run_id, user)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Streaming transition %s failed for run %s", name, run_id)
        raise HTTPException(500, f"{name} failed: {exc}") from exc
    if isinstance(result, tuple) and len(result) == 2:
        return result
    return bool(result), None


def _map_transition_error(error: object, action: str) -> HTTPException:
    detail = str(error) if error else f"{action} failed"
    lowered = detail.lower()
    if "not found" in lowered:
        return HTTPException(404, detail)
    if "conflict" in lowered or "concurrent" in lowered or "modified" in lowered:
        return HTTPException(409, detail)
    return HTTPException(400, detail)


@router.post("/runs/{run_id}/pause")
async def pause_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    await _get_run_or_404(run_id, current_user, db)
    ok, error = _call_transition("request_pause", run_id, current_user)
    if not ok:
        raise _map_transition_error(error, "pause")
    return {"status": "STREAMING_PAUSED", "run_id": run_id}


@router.post("/runs/{run_id}/resume")
async def resume_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    await _get_run_or_404(run_id, current_user, db)
    ok, error = _call_transition("request_resume", run_id, current_user)
    if not ok:
        raise _map_transition_error(error, "resume")
    return {"status": "STREAMING_ACTIVE", "run_id": run_id}


@router.post("/runs/{run_id}/stop")
async def stop_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    await _get_run_or_404(run_id, current_user, db)
    ok, error = _call_transition("request_stop", run_id, current_user)
    if not ok:
        raise _map_transition_error(error, "stop")
    return {"status": "STREAMING_STOPPED", "run_id": run_id}


@router.post("/runs/{run_id}/restart")
async def restart_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    await _get_run_or_404(run_id, current_user, db)
    ok, error = _call_transition("request_restart", run_id, current_user)
    if not ok:
        raise _map_transition_error(error, "restart")
    return {"status": "STREAMING_ACTIVE", "run_id": run_id}


@router.get("/runs/{run_id}/stats")
async def get_streaming_stats(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user, db)
    stats = db.query(StreamingStats).filter(StreamingStats.run_id == run_id).first()
    if not stats:
        return {"run_id": run_id, "status": run.status.value, "stats": None}
    return {
        "run_id": run_id,
        "status": run.status.value,
        "topic": stats.topic,
        "consumer_group": stats.consumer_group,
        "batches_processed": stats.batches_processed,
        "messages_processed": stats.messages_processed,
        "messages_failed": stats.messages_failed,
        "messages_dlq": stats.messages_dlq,
        "throughput_per_sec": stats.throughput_per_sec,
        "avg_batch_latency_ms": stats.avg_batch_latency_ms,
        "last_batch_at": stats.last_batch_at.isoformat() if stats.last_batch_at else None,
    }


@router.get("/topics")
async def list_topics(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    """List topics visible to the caller (M-006: scoped to caller's runs)."""
    try:
        topics = get_admin_client().list_topics()
    except Exception as exc:
        raise HTTPException(503, f"Cannot connect to Redpanda: {exc}") from exc
    if getattr(current_user, "role", None) == "admin":
        return {"topics": topics}
    try:
        owned = get_accessible_topics(current_user, db)
    except Exception:
        logger.exception("Topic ownership lookup failed; denying by default")
        owned = set()
    return {"topics": [t for t in topics if base_topic(t.get("name", "")) in owned]}


@router.post("/topics")
async def create_topic(
    topic: str,
    partitions: int = 8,
    current_user: User = Depends(get_current_admin),
):
    try:
        topic = validate_topic_name(topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    created = get_admin_client().create_topic(topic, partitions=partitions)
    return {"topic": topic, "partitions": partitions, "created": created}


@router.delete("/topics/{topic}")
async def delete_topic(
    topic: str,
    current_user: User = Depends(get_current_admin),
):
    try:
        topic = validate_topic_name(topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    get_admin_client().delete_topic(topic)
    return {"deleted": topic}


@router.get("/topics/{topic}/dlq")
async def inspect_dlq(
    topic: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    """Read messages from the Dead Letter Queue topic for inspection.

    M-006: scoped to the caller's runs (deny by default). M-063: the
    blocking consume runs in a worker thread and reads until idle.
    """
    try:
        topic = validate_topic_name(topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    limit = _clamp_limit(limit)
    if limit == 0:
        return {"topic": f"{topic}.dlq", "messages": [], "count": 0}
    dlq_topic = f"{topic}.dlq"
    admin = get_admin_client()
    try:
        exists = await asyncio.to_thread(admin.topic_exists, dlq_topic)
    except Exception as exc:
        raise HTTPException(503, f"Cannot connect to Redpanda: {exc}") from exc
    if not exists:
        return {"topic": dlq_topic, "messages": [], "count": 0}
    if not check_topic_access(current_user, topic, db):
        raise HTTPException(404, f"Topic not found: {topic}")
    try:
        messages = await asyncio.wait_for(
            asyncio.to_thread(_consume_dlq_sync, dlq_topic, limit),
            timeout=DLQ_INSPECT_TIMEOUT_SEC,
        )
    except TimeoutError as exc:
        raise HTTPException(503, f"DLQ inspect timed out: {dlq_topic}") from exc
    return {"topic": dlq_topic, "messages": messages, "count": len(messages)}


@router.post("/topics/{topic}/dlq/replay")
async def replay_dlq(
    topic: str,
    limit: int = 100,
    current_user: User = Depends(get_current_admin),
):
    """Replay DLQ messages back to the original topic for reprocessing."""
    try:
        topic = validate_topic_name(topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    limit = _clamp_limit(limit)
    dlq_topic = f"{topic}.dlq"
    admin = get_admin_client()
    try:
        exists = await asyncio.to_thread(admin.topic_exists, dlq_topic)
    except Exception as exc:
        raise HTTPException(503, f"Cannot connect to Redpanda: {exc}") from exc
    if not exists:
        raise HTTPException(404, f"DLQ topic not found: {dlq_topic}")
    if limit == 0:
        return {"replayed": 0, "from": dlq_topic, "to": topic}
    try:
        replayed = await asyncio.wait_for(
            asyncio.to_thread(_replay_dlq_sync, topic, dlq_topic, limit),
            timeout=DLQ_REPLAY_TIMEOUT_SEC,
        )
    except TimeoutError as exc:
        raise HTTPException(503, f"DLQ replay timed out: {dlq_topic}") from exc
    except RuntimeError as exc:
        # R-R-007: never report a phantom count — a failed flush means the
        # replay did not complete; the caller can retry (offsets committed
        # so far make the retry resume instead of duplicating everything).
        raise HTTPException(502, str(exc)) from exc
    return {"replayed": replayed, "from": dlq_topic, "to": topic}


async def _get_run_or_404(
    run_id: str, current_user: User, db: Session
) -> PipelineRun:
    from backend.utils.uuid_utils import as_uuid, validate_uuid_format

    validate_uuid_format(run_id)
    query = db.query(PipelineRun).filter(PipelineRun.id == as_uuid(run_id))
    # R-R-006: admins manage any run; the daemon-side transitions already
    # allow admins, so the route gate must too (was owner-only -> 404).
    if getattr(current_user, "role", None) != "admin":
        query = query.filter(PipelineRun.user_id == current_user.id)
    run = query.first()
    if not run:
        raise HTTPException(404, f"Run not found: {run_id}")
    return run
