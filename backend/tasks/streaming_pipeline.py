"""Long-running streaming pipeline Celery daemon.

Architectural differences from batch pipeline task:
  - NO time_limit / soft_time_limit — runs indefinitely until revoked
  - acks_late=True — acknowledge only after processing completes
  - Main loop polls Redpanda until self.is_aborted() returns True
  - consumer.close() ALWAYS called in finally block
  - Failed batches route to DLQ, pipeline continues (does NOT stop)
"""

import orjson
import logging
import time
from datetime import datetime, timezone

import pandas as pd
import pyarrow as pa
from celery.utils.log import get_task_logger
from confluent_kafka import KafkaError

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.db.redis_pools import get_pubsub_redis
from backend.execution.smart_executor import SmartExecutor
from backend.models import PipelineRun, PipelineStatus, StreamingStats
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.lineage import LineageRecorder
from backend.streaming.redpanda_client import (
    get_admin_client,
    get_producer_circuit_breaker,
    make_consumer,
    make_dlq_producer,
    make_producer,
    produce_with_fallback,
    try_drain_fallback,
    validate_topic_name,
)
from backend.utils.sse_security import sign_sse_payload
from backend.utils.time_utils import utcnow

logger = get_task_logger(__name__)

STATS_UPDATE_INTERVAL = 5
BACKPRESSURE_LAG_THRESHOLD = 50_000  # Pause consumer if lag exceeds 50K messages
BACKPRESSURE_COOLDOWN_SEC = 10       # Wait 10s before resuming after backpressure
IDEMPOTENT_CACHE_TTL = 10_000        # Keep last 10K processed offsets in memory
MAX_STREAMING_BATCH_SIZE = 50_000
MAX_CONSECUTIVE_ERRORS = 50          # Shut down after 50 consecutive batch errors




@celery_app.task(
    name="tasks.run_streaming_pipeline",
    queue="streaming",
    bind=True,
    acks_late=True,
)
def run_streaming_pipeline(self, run_id: str) -> dict:
    """Long-running streaming pipeline daemon.

    Loops forever polling a Redpanda topic until self.is_aborted() returns True.
    """
    logger.info("Streaming pipeline daemon started: run_id=%s", run_id)

    db = SessionLocal()
    consumer = None
    producer = None
    dlq_producer = None

    try:
        pipeline_run = db.query(PipelineRun).filter(
            PipelineRun.id == run_id).first()
        if not pipeline_run:
            logger.error("Run not found: %s", run_id)
            return {"error": "Run not found"}

        config = get_parsed_pipeline(pipeline_run.yaml_config)
        steps = config.steps

        consume_step = next(
            (s for s in steps if _step_type(s) == "stream_consume"), None)
        publish_step = next(
            (s for s in steps if _step_type(s) == "stream_publish"), None)
        middle_steps = [
            s for s in steps
            if _step_type(s) not in ("stream_consume", "stream_publish", "load", "save")
        ]

        if not consume_step:
            _set_status(db, run_id, PipelineStatus.FAILED)
            return {"error": "No stream_consume step found"}

        topic = _step_field(consume_step, "topic")
        consumer_group = _step_field(
            consume_step, "consumer_group") or f"piq-{run_id[:8]}"
        batch_size = max(1, min(int(_step_field(consume_step, "batch_size") or 1000), MAX_STREAMING_BATCH_SIZE))
        timeout_ms = int(_step_field(consume_step, "batch_timeout_ms") or 5000)
        timeout_s = timeout_ms / 1000.0
        deserialize_fmt = _step_field(consume_step, "deserialize") or "json"

        admin = get_admin_client()
        admin.ensure_topic(topic)

        _set_status(db, run_id, PipelineStatus.STREAMING_ACTIVE)
        _init_stats(db, run_id, topic, consumer_group)
        db.commit()

        consumer = make_consumer(consumer_group)
        consumer.subscribe([topic])

        producer = make_producer() if publish_step else None
        dlq_producer = make_dlq_producer()

        executor = SmartExecutor()
        recorder = LineageRecorder()

        stats = {
            "batches": 0,
            "messages": 0,
            "failed": 0,
            "dlq": 0,
            "start": time.monotonic(),
        }
        consecutive_errors = 0

        # Idempotent processing: track processed offsets to avoid duplicates
        processed_offsets: set = set()

        logger.info(
            "Subscribed to '%s' (group=%s, batch=%d, timeout=%.1fs)",
            topic, consumer_group, batch_size, timeout_s,
        )

        while not _is_aborted(db, run_id):
            if stats["batches"] % 10 == 0 and stats["batches"] > 0:
                if _handle_pause(db, run_id):
                    break

            # Backpressure detection: pause if consumer lag is too high
            lag = _compute_consumer_lag(consumer)
            if lag > BACKPRESSURE_LAG_THRESHOLD:
                logger.warning(
                    "BACKPRESSURE: consumer_lag=%d (threshold=%d), "
                    "cooling down %ds",
                    lag, BACKPRESSURE_LAG_THRESHOLD, BACKPRESSURE_COOLDOWN_SEC,
                )
                time.sleep(BACKPRESSURE_COOLDOWN_SEC)
                continue

            # Drain fallback queue if broker recovered
            try_drain_fallback(producer)

            messages = consumer.consume(
                num_messages=batch_size, timeout=timeout_s)
            if not messages:
                continue

            valid = [m for m in messages if not m.error()]
            errors = [
                m for m in messages
                if m.error() and m.error().code() != KafkaError._PARTITION_EOF
            ]

            if errors:
                logger.warning("Kafka partition errors: %d", len(errors))
            if not valid:
                continue

            # Idempotent processing: skip already-processed messages
            deduped = []
            for msg in valid:
                offset_key = (msg.topic(), msg.partition(), msg.offset())
                if offset_key not in processed_offsets:
                    deduped.append(msg)
                    processed_offsets.add(offset_key)
            # Trim cache to prevent unbounded growth
            if len(processed_offsets) > IDEMPOTENT_CACHE_TTL:
                # Remove oldest 50%
                to_remove = list(processed_offsets)[:len(processed_offsets)//2]
                for key in to_remove:
                    processed_offsets.discard(key)

            if not deduped:
                continue

            t0 = time.monotonic()

            df, malformed = _deserialize(deduped, deserialize_fmt)
            if malformed:
                _send_dlq(dlq_producer, [m for m, _ in malformed], topic, "deserialization failed")
                stats["dlq"] += len(malformed)

            if df.empty:
                consumer.commit(message=deduped[-1], asynchronous=False)
                continue

            try:
                table = pa.Table.from_pandas(df, preserve_index=False)
                table_registry: dict[str, pa.Table] = {"_stream_input": table}
                output_name = "_stream_input"

                for step in middle_steps:
                    result = executor.execute(
                        step,
                        table_registry,
                        recorder,
                    )
                    output_name = step.name if hasattr(step, "name") else output_name
                    table_registry[output_name] = result.output_table

                df = table_registry[output_name].to_pandas()
            except Exception as exc:
                logger.error("Batch processing error: %s", exc)
                _send_dlq(dlq_producer, deduped, topic, str(exc))
                stats["failed"] += len(deduped)
                stats["dlq"] += len(deduped)
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "Streaming pipeline exceeded max consecutive errors (%d), shutting down",
                        MAX_CONSECUTIVE_ERRORS,
                    )
                    break
                consumer.commit(message=deduped[-1], asynchronous=False)
                continue

            if publish_step and producer and not df.empty:
                try:
                    _publish(producer, df, publish_step)
                except Exception as exc:
                    logger.error("Publish error (non-fatal): %s", exc)

            batch_ms = (time.monotonic() - t0) * 1000
            stats["batches"] += 1
            stats["messages"] += len(deduped)
            consecutive_errors = 0
            elapsed = time.monotonic() - stats["start"]
            throughput = stats["messages"] / elapsed if elapsed > 0 else 0

            if stats["batches"] % STATS_UPDATE_INTERVAL == 0:
                _update_stats(db, run_id, stats, throughput, batch_ms, consumer)
                _sse_progress(run_id, stats, throughput)
            consumer.commit(message=deduped[-1], asynchronous=False)

        _set_status(db, run_id, PipelineStatus.STREAMING_STOPPED)
        logger.info(
            "Streaming stopped: batches=%d, messages=%d",
            stats["batches"], stats["messages"],
        )
        return stats

    except Exception as exc:
        logger.exception("Streaming error: %s", exc)
        try:
            _set_status(db, run_id, PipelineStatus.FAILED)
            db.commit()
        except Exception:
            pass
        raise
    finally:
        if consumer:
            consumer.close()
        if producer:
            producer.flush(timeout=5)
        if dlq_producer:
            dlq_producer.flush(timeout=5)
        db.close()
        logger.info("Consumer closed for run_id=%s", run_id)


def _step_type(step) -> str:
    val = getattr(step, "type", None) or getattr(step, "step_type", None)
    if hasattr(val, "value"):
        return val.value
    return str(val) if val else ""


def _step_field(step, field: str):
    return getattr(step, field, None)


def _deserialize(messages, fmt: str) -> tuple[pd.DataFrame, list[tuple[object, str]]]:
    records = []
    malformed = []
    for msg in messages:
        v = msg.value()
        if v is None:
            continue
        try:
            if fmt == "json":
                records.append(orjson.loads(v))
            else:
                records.append({"raw": v.decode("utf-8", errors="replace")})
        except Exception as exc:
            malformed.append((msg, str(exc)))
    return (pd.DataFrame(records) if records else pd.DataFrame()), malformed


def _publish(producer, df: pd.DataFrame, publish_step) -> None:
    topic = _step_field(publish_step, "topic")
    key_col = _step_field(publish_step, "key_column")
    for record in df.to_dict(orient="records"):
        key = (
            str(record[key_col]).encode()
            if key_col and key_col in record
            else None
        )
        value = orjson.dumps({k: str(v) if not isinstance(v, (str, int, float, bool, type(None), list, dict)) else v for k, v in record.items()})
        producer.produce(topic=topic, key=key, value=value)
    producer.poll(0)


def _send_dlq(dlq_producer, messages, original_topic: str, error: str) -> None:
    dlq_topic = f"{original_topic}.dlq"
    failed_at = datetime.now(timezone.utc).isoformat()
    for msg in messages:
        dlq_producer.produce(
            topic=dlq_topic,
            value=msg.value(),
            key=msg.key(),
            headers=[
                ("x-error", error[:500].encode()),
                ("x-original-topic", original_topic.encode()),
                ("x-failed-at", failed_at.encode()),
                ("x-original-offset", str(msg.offset()).encode()),
            ],
        )
    dlq_producer.flush(timeout=5)


def _is_aborted(db, run_id: str) -> bool:
    """Check if the streaming pipeline should stop (replaces deprecated self.is_aborted())."""
    try:
        db.expire_all()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return True
        return run.status not in (
            PipelineStatus.STREAMING_ACTIVE,
            PipelineStatus.STREAMING_PAUSED,
        )
    except Exception:
        return True


def _handle_pause(db, run_id: str) -> bool:
    db.expire_all()
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if not run:
        return True
    if run.status == PipelineStatus.STREAMING_PAUSED:
        logger.info("Paused — waiting for resume signal")
        while not _is_aborted(db, run_id):
            time.sleep(2)
            db.expire_all()
            run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
            if not run or run.status != PipelineStatus.STREAMING_PAUSED:
                break
        if _is_aborted(db, run_id):
            return True
        logger.info("Resumed")
    return False


def _set_status(db, run_id: str, status: PipelineStatus) -> None:
    db.query(PipelineRun).filter(PipelineRun.id == run_id).update(
        {"status": status}
    )
    db.commit()


def _init_stats(db, run_id: str, topic: str, consumer_group: str) -> None:
    db.add(StreamingStats(
        run_id=run_id,
        topic=topic,
        consumer_group=consumer_group,
    ))


def _update_stats(
    db, run_id: str, stats: dict, throughput: float, batch_ms: float,
    consumer=None,
) -> None:
    consumer_lag = _compute_consumer_lag(consumer) if consumer else 0
    db.query(StreamingStats).filter(
        StreamingStats.run_id == run_id
    ).update({
        "batches_processed": stats["batches"],
        "messages_processed": stats["messages"],
        "messages_failed": stats["failed"],
        "messages_dlq": stats["dlq"],
        "throughput_per_sec": round(throughput, 2),
        "avg_batch_latency_ms": round(batch_ms, 1),
        "consumer_lag": consumer_lag,
        "last_batch_at": utcnow(),
    })
    db.commit()


def _compute_consumer_lag(consumer) -> int:
    """Compute total consumer lag across all assigned partitions.

    Lag = sum(high_watermark - committed_offset) for each partition.
    Returns 0 if consumer not assigned or error occurs.
    """
    if consumer is None:
        return 0
    try:
        total_lag = 0
        assignments = consumer.assignment()
        if not assignments:
            return 0
        for tp in assignments:
            try:
                low, high = consumer.get_watermark_offsets(tp, timeout=1.0)
                committed = consumer.committed([tp], timeout=1.0)
                if committed and committed[0].offset >= 0:
                    lag = high - committed[0].offset
                    if lag > 0:
                        total_lag += lag
            except Exception:
                continue
        return total_lag
    except Exception:
        return 0


def _sse_progress(run_id: str, stats: dict, throughput: float) -> None:
    try:
        redis = get_pubsub_redis()
        channel = f"pipeline_progress:{run_id}"
        payload = {
            "event": "streaming_stats",
            "run_id": str(run_id),
            "batches": stats["batches"],
            "messages": stats["messages"],
            "dlq": stats["dlq"],
            "throughput": round(throughput, 2),
        }
        redis.publish(channel, orjson.dumps(sign_sse_payload(payload)))
    except Exception:
        pass
