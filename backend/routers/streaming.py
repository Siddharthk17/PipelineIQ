"""Streaming pipeline control and Dead Letter Queue management API."""

import orjson
import logging
import time
import uuid as uuid_module

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.auth import get_current_admin, get_current_user
from backend.celery_app import celery_app
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PipelineRun, StreamingStats, User
from backend.streaming.redpanda_client import (
    get_admin_client,
    make_consumer,
    make_producer,
    validate_topic_name,
)

router = APIRouter(prefix="/api/streaming", tags=["Streaming"])
logger = logging.getLogger(__name__)


@router.post("/runs/{run_id}/pause")
async def pause_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user.id, db)
    if run.status.value != "STREAMING_ACTIVE":
        raise HTTPException(
            400, f"Run not streaming_active (status: {run.status.value})")
    db.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id)
        .values(status="STREAMING_PAUSED")
    )
    db.commit()
    return {"status": "STREAMING_PAUSED", "run_id": run_id}


@router.post("/runs/{run_id}/resume")
async def resume_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user.id, db)
    if run.status.value != "STREAMING_PAUSED":
        raise HTTPException(
            400, f"Run not streaming_paused (status: {run.status.value})")
    db.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id)
        .values(status="STREAMING_ACTIVE")
    )
    db.commit()
    return {"status": "STREAMING_ACTIVE", "run_id": run_id}


@router.post("/runs/{run_id}/stop")
async def stop_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user.id, db)
    if run.status.value not in ("STREAMING_ACTIVE", "STREAMING_PAUSED"):
        raise HTTPException(
            400, f"Run not streaming (status: {run.status.value})")
    if run.celery_task_id:
        celery_app.control.revoke(
            run.celery_task_id, terminate=True, signal="SIGTERM")
    db.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id)
        .values(status="STREAMING_STOPPED")
    )
    db.commit()
    return {"status": "STREAMING_STOPPED", "run_id": run_id}


@router.post("/runs/{run_id}/restart")
async def restart_streaming(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user.id, db)
    if run.status.value != "STREAMING_STOPPED":
        raise HTTPException(
            400, f"Run not stopped (status: {run.status.value})")
    from backend.tasks.streaming_pipeline import run_streaming_pipeline
    result = run_streaming_pipeline.delay(run_id)
    db.execute(
        update(PipelineRun)
        .where(PipelineRun.id == run_id)
        .values(status="STREAMING_ACTIVE", celery_task_id=result.id)
    )
    db.commit()
    return {"status": "STREAMING_ACTIVE", "run_id": run_id, "celery_task_id": result.id}


@router.get("/runs/{run_id}/stats")
async def get_streaming_stats(
    run_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    run = await _get_run_or_404(run_id, current_user.id, db)
    stats = db.query(StreamingStats).filter(
        StreamingStats.run_id == run_id).first()
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
async def list_topics(current_user: User = Depends(get_current_user)):
    try:
        return {"topics": get_admin_client().list_topics()}
    except Exception as exc:
        raise HTTPException(503, f"Cannot connect to Redpanda: {exc}")


@router.post("/topics")
async def create_topic(
    topic: str,
    partitions: int = 8,
    current_user: User = Depends(get_current_admin),
):
    try:
        topic = validate_topic_name(topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
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
        raise HTTPException(400, str(exc))
    get_admin_client().delete_topic(topic)
    return {"deleted": topic}


@router.get("/topics/{topic}/dlq")
async def inspect_dlq(
    topic: str,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
):
    """Read messages from the Dead Letter Queue topic for inspection."""
    dlq_topic = f"{topic}.dlq"
    admin = get_admin_client()
    if not admin.topic_exists(dlq_topic):
        return {"topic": dlq_topic, "messages": [], "count": 0}

    group = f"dlq-inspector-{uuid_module.uuid4().hex[:8]}"
    consumer = make_consumer(group, auto_offset_reset="earliest")
    consumer.subscribe([dlq_topic])
    messages = []
    t_start = time.time()

    while len(messages) < limit and (time.time() - t_start) < 5.0:
        batch = consumer.consume(
            num_messages=min(limit - len(messages), 50), timeout=1.0)
        if not batch:
            break
        for msg in batch:
            if msg.error():
                continue
            headers = {
                k: v.decode("utf-8", errors="replace") if v else ""
                for k, v in (msg.headers() or [])
            }
            try:
                val = msg.value().decode("utf-8", errors="replace")
                try:
                    val = orjson.loads(val)
                except Exception:
                    pass
            except Exception:
                val = "<binary>"
            messages.append({
                "partition": msg.partition(),
                "offset": msg.offset(),
                "key": msg.key().decode("utf-8", errors="replace") if msg.key() else None,
                "value": val,
                "error": headers.get("x-error"),
                "original_topic": headers.get("x-original-topic"),
                "failed_at": headers.get("x-failed-at"),
            })
    consumer.close()
    return {"topic": dlq_topic, "messages": messages, "count": len(messages)}


@router.post("/topics/{topic}/dlq/replay")
async def replay_dlq(
    topic: str,
    limit: int = 100,
    current_user: User = Depends(get_current_admin),
):
    """Replay DLQ messages back to the original topic for reprocessing."""
    dlq_topic = f"{topic}.dlq"
    admin = get_admin_client()
    if not admin.topic_exists(dlq_topic):
        raise HTTPException(404, f"DLQ topic not found: {dlq_topic}")

    admin.ensure_topic(topic)
    group = f"dlq-replay-{uuid_module.uuid4().hex[:8]}"
    consumer = make_consumer(group, auto_offset_reset="earliest")
    consumer.subscribe([dlq_topic])
    producer = make_producer()
    replayed = 0
    t_start = time.time()

    while replayed < limit and (time.time() - t_start) < 30.0:
        batch = consumer.consume(
            num_messages=min(limit - replayed, 100), timeout=2.0)
        if not batch:
            break
        for msg in batch:
            if not msg.error():
                producer.produce(topic=topic, value=msg.value(), key=msg.key())
                replayed += 1
    producer.flush(timeout=10)
    consumer.close()
    return {"replayed": replayed, "from": dlq_topic, "to": topic}


async def _get_run_or_404(run_id: str, user_id, db: Session) -> PipelineRun:
    run = db.query(PipelineRun).filter(
        PipelineRun.id == run_id,
        PipelineRun.user_id == user_id,
    ).first()
    if not run:
        raise HTTPException(404, f"Run not found: {run_id}")
    return run
