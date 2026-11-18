"""Long-running streaming pipeline Celery daemon.

Architectural differences from batch pipeline task:
  - NO time_limit / soft_time_limit — runs indefinitely until revoked
  - acks_late=True — acknowledge only after processing completes
  - Main loop polls Redpanda until self.is_aborted() returns True
  - consumer.close() ALWAYS called in finally block
  - Failed batches route to DLQ, pipeline continues (does NOT stop)
"""

import time
from datetime import UTC, datetime

import orjson
import pandas as pd
import pyarrow as pa
from celery import Task
from celery.utils.log import get_task_logger
from confluent_kafka import KafkaError, TopicPartition
from sqlalchemy.exc import IntegrityError

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.db.redis_pools import get_pubsub_redis
from backend.execution.smart_executor import SmartExecutor
from backend.models import PipelineRun, PipelineStatus, StreamingStats
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.lineage import LineageRecorder
from backend.streaming.redpanda_client import (
    get_admin_client,
    make_consumer,
    make_dlq_producer,
    make_producer,
    try_drain_fallback,
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


class StreamingPublishError(RuntimeError):
    """Raised when a batch cannot be durably published to the output topic."""


class StreamingDLQError(RuntimeError):
    """Raised when a failed batch cannot be durably preserved in the DLQ."""


class _UnlimitedTask(Task):
    """Base for daemon tasks that must never be time-limited.

    Celery >= 5.7 copies task_time_limit/task_soft_time_limit onto every
    task at bind time, and app finalization re-binds every task, so neither
    omitting the options nor task_annotations can keep them None.
    on_bound runs after the merge on every bind, so clearing here sticks.
    """

    abstract = True

    @classmethod
    def on_bound(cls, app) -> None:
        cls.time_limit = None
        cls.soft_time_limit = None


@celery_app.task(
    base=_UnlimitedTask,
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
        executor._current_run_id = run_id
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
            if (
                stats["batches"] % 10 == 0
                and stats["batches"] > 0
                and _handle_pause(db, run_id)
            ):
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

            message_errors = [(m, m.error()) for m in messages]
            valid = [m for m, err in message_errors if err is None]
            errors = [
                m for m, err in message_errors
                if err is not None and err.code() != KafkaError._PARTITION_EOF
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

            def _on_success(stats, throughput, batch_ms):
                if stats["batches"] % STATS_UPDATE_INTERVAL == 0:
                    _persist_stats_best_effort(
                        db, run_id, stats, throughput, batch_ms, consumer)
                    _sse_progress(run_id, stats, throughput)

            consecutive_errors = _process_batch(
                deduped,
                consumer=consumer,
                producer=producer,
                dlq_producer=dlq_producer,
                consume_step=consume_step,
                deserialize_fmt=deserialize_fmt,
                publish_step=publish_step,
                middle_steps=middle_steps,
                executor=executor,
                recorder=recorder,
                stats=stats,
                consecutive_errors=consecutive_errors,
                on_success=_on_success,
            )
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                _fail_run(
                    db, run_id,
                    "Streaming pipeline exceeded max consecutive batch "
                    f"errors ({MAX_CONSECUTIVE_ERRORS}); shutting down",
                )
                break
            if consecutive_errors and _sleep_interruptible(
                min(consecutive_errors, 10), db, run_id,
            ):
                break

        _set_status(db, run_id, PipelineStatus.STREAMING_STOPPED)
        logger.info(
            "Streaming stopped: batches=%d, messages=%d",
            stats["batches"], stats["messages"],
        )
    except Exception:
        logger.exception("Streaming error")
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
    return stats


def _step_type(step) -> str:
    val = getattr(step, "type", None) or getattr(step, "step_type", None)
    if val is not None and hasattr(val, "value"):
        return val.value
    return str(val) if val else ""


def _step_field(step, field: str):
    return getattr(step, field, None)


def _seed_registry(
    table: "pa.Table", consume_step,
) -> tuple[dict[str, "pa.Table"], str]:
    """Seed the per-batch table registry (M-009).

    Transforms name the consume step as their ``input`` (e.g.
    ``input: ingest``), but the daemon used to seed only the
    ``_stream_input`` alias, so the first transform raised KeyError and
    every batch went to the DLQ. Seed the consume step's output under
    the consume step's own name and keep ``_stream_input`` as a legacy
    alias for pipelines that reference it.
    """
    consume_name = getattr(consume_step, "name", None) or "_stream_input"
    registry: dict[str, pa.Table] = {consume_name: table}
    registry.setdefault("_stream_input", table)
    return registry, consume_name


def _ensure_step_input(registry: dict, step, fallback_name: str) -> None:
    """Chain single-input steps whose ``input`` is unresolvable (M-009).

    When a step names an input absent from the registry, alias it to the
    previous step's output (with a warning) instead of letting the
    executor raise KeyError and DLQing the batch. Join steps carry
    ``left``/``right`` refs rather than a single ``input`` and are left
    alone: a missing join side is a real config error that must surface.
    """
    input_name = getattr(step, "input", None)
    if (
        input_name
        and input_name not in registry
        and fallback_name in registry
    ):
        logger.warning(
            "Step '%s' names unknown input '%s'; chaining from '%s'",
            getattr(step, "name", "?"), input_name, fallback_name,
        )
        registry[input_name] = registry[fallback_name]


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
    """Publish a batch and wait until it is durably accepted (M-010).

    Uses delivery callbacks plus a blocking flush: any delivery failure
    or undelivered message raises :class:`StreamingPublishError` so the
    caller withholds the offset commit instead of silently losing data.
    """
    topic = _step_field(publish_step, "topic")
    if not topic:
        raise StreamingPublishError("publish step has no topic configured")
    key_col = _step_field(publish_step, "key_column")
    delivery_errors: list = []

    def _on_delivery(err, _msg) -> None:
        if err is not None:
            delivery_errors.append(err)

    try:
        for record in df.to_dict(orient="records"):
            key = (
                str(record[key_col]).encode()
                if key_col and key_col in record
                else None
            )
            value = orjson.dumps({k: str(v) if not isinstance(v, (str, int, float, bool, type(None), list, dict)) else v for k, v in record.items()})
            producer.produce(
                topic=topic, key=key, value=value,
                on_delivery=_on_delivery,
            )
    except Exception as exc:
        raise StreamingPublishError(
            f"Failed to queue batch for topic '{topic}': {exc}") from exc
    producer.poll(0)
    remaining = producer.flush(timeout=30)
    if delivery_errors:
        raise StreamingPublishError(
            f"{len(delivery_errors)} deliveries to '{topic}' failed: "
            f"{delivery_errors[0]}")
    if remaining:
        raise StreamingPublishError(
            f"{remaining} messages to '{topic}' undelivered after flush")


def _send_dlq(dlq_producer, messages, original_topic: str, error: str) -> None:
    """Preserve failed messages in the DLQ, raising if not durable (M-010).

    Raises :class:`StreamingDLQError` when the DLQ write cannot be
    confirmed, so the caller withholds the offset commit (the batch will
    redeliver) instead of dropping it.
    """
    if not messages:
        return
    dlq_topic = f"{original_topic}.dlq"
    failed_at = datetime.now(UTC).isoformat()
    delivery_errors: list = []

    def _on_delivery(err, _msg) -> None:
        if err is not None:
            delivery_errors.append(err)

    try:
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
                on_delivery=_on_delivery,
            )
    except Exception as exc:
        raise StreamingDLQError(
            f"Failed to queue {len(messages)} messages for DLQ "
            f"'{dlq_topic}': {exc}") from exc
    dlq_producer.poll(0)
    remaining = dlq_producer.flush(timeout=10)
    if delivery_errors:
        raise StreamingDLQError(
            f"{len(delivery_errors)} DLQ deliveries to '{dlq_topic}' "
            f"failed: {delivery_errors[0]}")
    if remaining:
        raise StreamingDLQError(
            f"{remaining} messages to DLQ '{dlq_topic}' undelivered "
            "after flush")


def _commit_offsets(consumer, messages) -> None:
    """Commit the per-partition high-water mark for a batch (M-010).

    The old code committed a single message's offset, silently leaving
    sibling partitions uncommitted; commit offset+1 for every partition
    present in the batch instead.
    """
    high: dict = {}
    for msg in messages:
        key = (msg.topic(), msg.partition())
        if msg.offset() > high.get(key, -1):
            high[key] = msg.offset()
    if not high:
        return
    consumer.commit(
        offsets=[
            TopicPartition(topic, partition, offset + 1)
            for (topic, partition), offset in high.items()
        ],
        asynchronous=False,
    )


def _process_batch(
    messages,
    *,
    consumer,
    producer,
    dlq_producer,
    consume_step,
    deserialize_fmt: str,
    publish_step,
    middle_steps,
    executor,
    recorder,
    stats: dict,
    consecutive_errors: int,
    on_success=None,
) -> int:
    """Process one batch; commit offsets only on durable success (M-010).

    Returns the updated consecutive-error count. Offsets are committed
    exactly on paths where the batch was fully published *and* persisted
    (DLQ for malformed/failed records). On publish/DLQ persistence
    failure the commit is withheld so the batch redelivers instead of
    being silently lost.
    """
    topic = _step_field(consume_step, "topic")
    t0 = time.monotonic()
    try:
        df, malformed = _deserialize(messages, deserialize_fmt)
        if malformed:
            _send_dlq(
                dlq_producer, [m for m, _ in malformed],
                topic, "deserialization failed",
            )
            stats["dlq"] += len(malformed)

        if not df.empty:
            table = pa.Table.from_pandas(df, preserve_index=False)
            table_registry, output_name = _seed_registry(table, consume_step)
            for step in middle_steps:
                _ensure_step_input(table_registry, step, output_name)
                result = executor.execute(step, table_registry, recorder)
                output_name = (
                    step.name if hasattr(step, "name") else output_name)
                table_registry[output_name] = result.output_table
            df = table_registry[output_name].to_pandas()
            if publish_step is not None and producer is not None and not df.empty:
                _publish(producer, df, publish_step)

        batch_ms = (time.monotonic() - t0) * 1000
        stats["batches"] += 1
        stats["messages"] += len(messages)
        elapsed = time.monotonic() - stats["start"]
        throughput = stats["messages"] / elapsed if elapsed > 0 else 0
        if on_success is not None:
            on_success(stats=stats, throughput=throughput, batch_ms=batch_ms)
        _commit_offsets(consumer, messages)
    except (StreamingPublishError, StreamingDLQError):
        logger.exception(
            "Batch persist failed; withholding offset commit for redelivery")
        stats["failed"] += len(messages)
        return consecutive_errors + 1
    except Exception as exc:
        # Transform (or unexpected) error: preserve the batch in the DLQ
        # and commit only if that preservation succeeded.
        try:
            _send_dlq(dlq_producer, messages, topic, str(exc))
        except StreamingDLQError:
            logger.exception(
                "Batch failed and DLQ persist failed; "
                "withholding offset commit for redelivery")
            stats["failed"] += len(messages)
            return consecutive_errors + 1
        logger.exception("Batch processing error; batch preserved in DLQ")
        stats["failed"] += len(messages)
        stats["dlq"] += len(messages)
        _commit_offsets(consumer, messages)
        return consecutive_errors + 1
    else:
        return 0


def _persist_stats_best_effort(
    db, run_id: str, stats: dict, throughput: float, batch_ms: float,
    consumer=None,
) -> None:
    """Persist stats without endangering the batch (M-010/M-044).

    Stats are observability, not data: a transient DB failure rolls back
    and logs instead of failing the batch (whose offsets are already
    committed) or poisoning the session for subsequent iterations.
    """
    try:
        _update_stats(db, run_id, stats, throughput, batch_ms, consumer)
    except Exception:
        db.rollback()
        logger.warning(
            "Streaming stats persist failed (best-effort); continuing",
            exc_info=True,
        )


def _fail_run(db, run_id: str, reason: str) -> None:
    """Mark a run FAILED with a reason instead of stranding it (M-044)."""
    logger.critical("Streaming run %s failed: %s", run_id, reason)
    try:
        db.rollback()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if run is not None:
            run.status = PipelineStatus.FAILED
            run.error_message = reason[:2000]
            run.completed_at = utcnow()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to mark run %s FAILED", run_id)


def _sleep_interruptible(seconds: float, db, run_id: str) -> bool:
    """Sleep up to ``seconds``, waking early if the run aborts.

    Returns True when the daemon should stop.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if _is_aborted(db, run_id):
            return True
        time.sleep(min(1.0, deadline - time.monotonic()))
    return _is_aborted(db, run_id)


def _is_aborted(db, run_id: str) -> bool:
    """Check if the streaming pipeline should stop (replaces deprecated self.is_aborted())."""
    try:
        db.expire_all()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return True
        aborted = run.status not in (
            PipelineStatus.STREAMING_ACTIVE,
            PipelineStatus.STREAMING_PAUSED,
        )
    except Exception:
        return True
    else:
        return aborted


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
    """Create the stats row, or reset it when restarting (M-011).

    Restart reuses the same run_id whose PK row already exists; the old
    unconditional insert raised IntegrityError and failed the run after
    the restart endpoint had already replied success. Reset counters so
    a restarted run reports fresh cumulative stats.
    """
    row = db.query(StreamingStats).filter(
        StreamingStats.run_id == run_id).first()
    if row is None:
        row = StreamingStats(
            run_id=run_id,
            topic=topic,
            consumer_group=consumer_group,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            # Lost a race with a duplicate daemon (see M-042
            # single-flight): adopt the winner's row instead of dying.
            db.rollback()
            row = db.query(StreamingStats).filter(
                StreamingStats.run_id == run_id).first()
            if row is None:
                raise
        else:
            return
    row.topic = topic
    row.consumer_group = consumer_group
    row.batches_processed = 0
    row.messages_processed = 0
    row.messages_failed = 0
    row.messages_dlq = 0
    row.throughput_per_sec = None
    row.consumer_lag = None
    row.avg_batch_latency_ms = None
    row.last_batch_at = None


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
                _low, high = consumer.get_watermark_offsets(tp, timeout=1.0)
                committed = consumer.committed([tp], timeout=1.0)
                if committed and committed[0].offset >= 0:
                    lag = high - committed[0].offset
                    if lag > 0:
                        total_lag += lag
            except Exception:
                continue
    except Exception:
        return 0
    else:
        return total_lag


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
