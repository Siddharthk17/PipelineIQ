"""Long-running streaming pipeline Celery daemon.

Architectural differences from batch pipeline task:
  - NO time_limit / soft_time_limit — runs indefinitely until revoked
  - acks_late=True — acknowledge only after processing completes
  - Main loop polls Redpanda until self.is_aborted() returns True
  - consumer.close() ALWAYS called in finally block
  - Failed batches route to DLQ, pipeline continues (does NOT stop)
"""

import contextlib
import time
import uuid
from datetime import UTC, datetime

import orjson
import pandas as pd
import pyarrow as pa
from celery import Task
from celery.utils.log import get_task_logger
from confluent_kafka import KafkaError, TopicPartition
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.db.redis_pools import get_pubsub_redis
from backend.execution.smart_executor import SmartExecutor
from backend.models import (
    DataAsset,
    PipelineRun,
    PipelineStatus,
    StreamingStats,
    User,
)
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.lineage import LineageRecorder
from backend.streaming.redpanda_client import (
    get_admin_client,
    make_consumer,
    make_dlq_producer,
    make_producer,
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
# Explicit bound on how long the broker may wait before redelivering the
# daemon's still-unacked (acks_late) task message (M-042). The daemon runs
# indefinitely, so *some* redelivery bound always applies; making it
# explicit documents the design and keeps redeliveries frequent enough to
# recover a dead worker but rare enough to avoid duplicate-start churn.
# Every redelivery is safe: _claim_run single-flights them (only the first
# claimant runs; losers exit immediately without mutating the run).
STREAMING_VISIBILITY_TIMEOUT_S = 1800
# Pause-wait poll quantum: stop/revoke wakes a paused daemon within ~1s,
# status is re-read every quantum (M-152: a paused daemon stays cancellable).
_PAUSE_POLL_SEC = 2


class StreamingPublishError(RuntimeError):
    """Raised when a batch cannot be durably published to the output topic."""


class StreamingDLQError(RuntimeError):
    """Raised when a failed batch cannot be durably preserved in the DLQ."""


class StreamingCommitError(RuntimeError):
    """Raised when offsets cannot be committed after bounded retries.

    Unlike publish/DLQ failures this must NOT route the batch to the DLQ:
    the batch was already published, so DLQ-ing it would both poison the
    DLQ with healthy data and (via the generic handler) wedge the daemon
    on a transient broker blip (R2-001). Callers back off and let the
    daemon loop decide; redelivery on restart may duplicate already
    published rows (at-least-once), never lose them.
    """


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
    visibility_timeout=STREAMING_VISIBILITY_TIMEOUT_S,
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
        task_id = getattr(getattr(self, "request", None), "id", None)
        denial = _claim_run(db, run_id, task_id)
        if denial is not None:
            logger.warning("Streaming daemon refused to start: %s", denial)
            return {"error": denial}

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
        consumer_group = _daemon_group_id(
            run_id, _step_field(consume_step, "consumer_group"))
        batch_size = max(1, min(int(_step_field(consume_step, "batch_size") or 1000), MAX_STREAMING_BATCH_SIZE))
        timeout_ms = int(_step_field(consume_step, "batch_timeout_ms") or 5000)
        timeout_s = timeout_ms / 1000.0
        deserialize_fmt = _step_field(consume_step, "deserialize") or "json"
        # Offset choice is explicit and logged (M-131): "earliest" replays
        # backlog waiting on the topic, "latest" processes only new
        # messages. Anything else falls back to earliest (never skip).
        offset_reset = _step_field(consume_step, "auto_offset_reset")

        # Batch-only steps are rejected explicitly instead of being
        # silently filtered out of the batch (M-160).
        step_error = _validate_streaming_steps(steps)
        if step_error is not None:
            _fail_run(db, run_id, step_error)
            return {"error": step_error}

        # The publish path enforces topic ownership (M-006): fail fast
        # before creating topics or consuming a single message.
        access_error = _require_topic_access(
            db, pipeline_run,
            {
                "consume": topic,
                "publish": (
                    _step_field(publish_step, "topic")
                    if publish_step is not None else None
                ),
            },
        )
        if access_error is not None:
            _fail_run(db, run_id, access_error)
            return {"error": access_error}

        # First-use provisioning (M-006): record ownership of the topics
        # this run just claimed so the next run's check can deny foreign
        # claimants. Registration failure only warns (the next startup
        # retries); it never blocks the run.
        _register_topic_assets(
            db, pipeline_run.user_id,
            [topic] + (
                [_step_field(publish_step, "topic")]
                if publish_step is not None else []
            ),
        )

        admin = get_admin_client()
        admin.ensure_topic(topic)

        # Status was already claimed as STREAMING_ACTIVE by _claim_run.
        _init_stats(db, run_id, topic, consumer_group)
        db.commit()

        consumer = _daemon_consumer(consumer_group, offset_reset)
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
        # Insertion-ordered (dict used as an ordered set) so the trim
        # below evicts oldest-first instead of an arbitrary hash-ordered
        # half (R-R-010).
        processed_offsets: dict = {}

        logger.info(
            "Subscribed to '%s' (group=%s, batch=%d, timeout=%.1fs)",
            topic, consumer_group, batch_size, timeout_s,
        )

        while not _is_aborted(db, run_id):
            # Pause is checked every iteration (not every N batches) so
            # pause/stop take effect within one batch (M-152).
            if _handle_pause(db, run_id):
                break

            # Backpressure: pause fetching (never skip backlog silently),
            # with an interruptible cooldown and an SSE event (M-131).
            if _apply_backpressure(consumer, db, run_id):
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

            # Idempotent processing: skip already-processed messages.
            # R-R-010: evict oldest-first (insertion order), never an
            # arbitrary hash-ordered half — evicting the newest batch's
            # offsets would let its immediate redelivery double-process.
            deduped = []
            for msg in valid:
                offset_key = (msg.topic(), msg.partition(), msg.offset())
                if offset_key not in processed_offsets:
                    deduped.append(msg)
                    processed_offsets[offset_key] = None
            # Trim cache to prevent unbounded growth
            if len(processed_offsets) > IDEMPOTENT_CACHE_TTL:
                # Remove oldest 50% (dicts preserve insertion order)
                for key in list(processed_offsets)[:len(processed_offsets)//2]:
                    del processed_offsets[key]

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

        # Settle without clobbering terminal states: a max-errors shutdown
        # already marked FAILED with a reason; a stop/revoke already marked
        # STOPPED/CANCELLED. Only daemon-owned states become STOPPED (M-078).
        _settle_final_status(db, run_id)
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


# ---------------------------------------------------------------------------
# Operator transitions (M-078).
#
# The streaming control routes (pause/resume/stop/restart) delegate to the
# request_* functions below so the state machine lives with the daemon that
# enforces it. Every transition is an atomic conditional UPDATE
# (check-and-set): concurrent requests serialize on the row and all but one
# lose with a conflict instead of silently toggling state.
#
#   ACTIVE  --request_pause-->  PAUSED
#   PAUSED  --request_resume--> ACTIVE
#   ACTIVE | PAUSED --request_stop--> STREAMING_STOPPED (+ revoke)
#   STREAMING_STOPPED | CANCELLED | FAILED --request_restart--> ACTIVE (+ fresh daemon dispatch)
#
# FAILED is restartable by design (R2-001): a transient offset-commit
# failure marks the run FAILED (honest, restartable) instead of wedging
# it; restart dispatches a fresh daemon from the last committed offset
# (at-least-once redelivery, never loss).
#
# Each function returns (ok, error): (True, None) on success, otherwise
# (False, human-readable reason). Foreign runs are reported as "not found"
# (never 403) so run IDs cannot be probed, mirroring the run-ownership rule.
# ---------------------------------------------------------------------------

_PAUSE_FROM = frozenset({PipelineStatus.STREAMING_ACTIVE})
_RESUME_FROM = frozenset({PipelineStatus.STREAMING_PAUSED})
_STOP_FROM = frozenset({
    PipelineStatus.STREAMING_ACTIVE, PipelineStatus.STREAMING_PAUSED,
})
_RESTART_FROM = frozenset({PipelineStatus.STREAMING_STOPPED, PipelineStatus.CANCELLED, PipelineStatus.FAILED})


def _coerce_run_uuid(run_id):
    """Parse a run id, returning None when it cannot name a run."""
    try:
        return run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    except (ValueError, AttributeError, TypeError):
        return None


def _transition_session(db):
    """Use the caller's session, or open (and own) a fresh one."""
    if db is not None:
        return db, False
    return SessionLocal(), True


def _check_streaming_access(run, user) -> str | None:
    """None when `user` may transition `run`, else a denial reason.

    The run owner or an admin may act; anyone else gets "not found" so
    the existence of foreign runs is not disclosed.
    """
    if getattr(user, "role", None) == "admin":
        return None
    actor_id = getattr(user, "id", None)
    if (
        actor_id is not None
        and run.user_id is not None
        and str(run.user_id) == str(actor_id)
    ):
        return None
    return f"Run not found: {run.id}"


def _atomic_transition(db, run_uuid, expected, to_status, extra=None) -> bool:
    """Conditional UPDATE to `to_status`; True iff this caller won the race."""
    values = {"status": to_status}
    if extra:
        values.update(extra)
    updated = (
        db.query(PipelineRun)
        .filter(
            PipelineRun.id == run_uuid,
            PipelineRun.status.in_(expected),
        )
        .update(values)
    )
    if updated != 1:
        db.rollback()
        return False
    db.commit()
    return True


def _load_transition_run(session, run_id):
    """Fetch the run for a transition, or an error when it cannot be named."""
    run_uuid = _coerce_run_uuid(run_id)
    if run_uuid is None:
        return None, None, f"Run not found: {run_id}"
    run = session.query(PipelineRun).filter(
        PipelineRun.id == run_uuid).first()
    if run is None:
        return None, None, f"Run not found: {run_id}"
    return run, run_uuid, None


def _transition_conflict(session, run_uuid, run_id, action: str, expected: str) -> str:
    """Fresh conflict message naming the run's actual current status."""
    session.expire_all()
    run = session.query(PipelineRun).filter(
        PipelineRun.id == run_uuid).first()
    actual = run.status.value if run is not None else "missing"
    return (
        f"state conflict: cannot {action} run {run_id} "
        f"(status {actual}, expected {expected})"
    )


def request_pause(run_id, user, db=None) -> tuple:
    """Transition STREAMING_ACTIVE -> STREAMING_PAUSED (M-078)."""
    session, owned = _transition_session(db)
    try:
        run, run_uuid, error = _load_transition_run(session, run_id)
        if error is not None:
            return False, error
        denial = _check_streaming_access(run, user)
        if denial is not None:
            return False, denial
        if not _atomic_transition(
            session, run_uuid, _PAUSE_FROM, PipelineStatus.STREAMING_PAUSED,
        ):
            return False, _transition_conflict(
                session, run_uuid, run_id, "pause", "STREAMING_ACTIVE")
        logger.info("Streaming run %s paused by operator", run_id)
        return True, None
    finally:
        if owned:
            session.close()


def request_resume(run_id, user, db=None) -> tuple:
    """Transition STREAMING_PAUSED -> STREAMING_ACTIVE (M-078).

    After the atomic claim, ensure a daemon is actually running: a paused
    daemon whose worker died leaves no task behind, and merely flipping
    the row would park the run ACTIVE-with-nothing-running until the
    reconciler TIMEOUTs it (R2-002). If the recorded task is dead or
    missing, dispatch a fresh daemon like request_restart does.
    """
    from celery.result import AsyncResult as _AsyncResult

    session, owned = _transition_session(db)
    try:
        run, run_uuid, error = _load_transition_run(session, run_id)
        if error is not None:
            return False, error
        denial = _check_streaming_access(run, user)
        if denial is not None:
            return False, denial
        if not _atomic_transition(
            session, run_uuid, _RESUME_FROM, PipelineStatus.STREAMING_ACTIVE,
        ):
            return False, _transition_conflict(
                session, run_uuid, run_id, "resume", "STREAMING_PAUSED")
        logger.info("Streaming run %s resumed by operator", run_id)
        task_id = run.celery_task_id
        alive = False
        if task_id:
            try:
                # R2-002: only STARTED/RETRY prove liveness. PENDING is
                # ambiguous (queued-live vs unknown/expired) so it must NOT
                # count as alive — dispatching a duplicate is safe (the
                # daemon single-flight in _claim_run lets exactly one win).
                alive = _AsyncResult(str(task_id)).state in ("STARTED", "RETRY")
            except Exception:
                alive = False
        if not alive:
            try:
                dispatched = run_streaming_pipeline.delay(str(run_uuid))
            except Exception as exc:
                return False, f"failed to dispatch daemon for run {run_id}: {exc}"
            new_task_id = getattr(dispatched, "id", None)
            session.query(PipelineRun).filter(
                PipelineRun.id == run_uuid
            ).update({"celery_task_id": new_task_id})
            try:
                session.commit()
            except Exception:
                session.rollback()
        return True, None
    finally:
        if owned:
            session.close()


def request_stop(run_id, user, db=None) -> tuple:
    """Transition STREAMING_ACTIVE/PAUSED -> STREAMING_STOPPED (M-078).

    The status flip lands first (the daemon exits cooperatively on its
    next poll even if the revoke below fails); the Celery revoke then
    expedites the shutdown best-effort.
    """
    session, owned = _transition_session(db)
    try:
        run, run_uuid, error = _load_transition_run(session, run_id)
        if error is not None:
            return False, error
        denial = _check_streaming_access(run, user)
        if denial is not None:
            return False, denial
        task_id = run.celery_task_id
        if not _atomic_transition(
            session, run_uuid, _STOP_FROM, PipelineStatus.STREAMING_STOPPED,
        ):
            return False, _transition_conflict(
                session, run_uuid, run_id, "stop",
                "STREAMING_ACTIVE or STREAMING_PAUSED")
        try:
            celery_app.control.revoke(
                task_id or str(run_uuid), terminate=True, signal="SIGTERM")
        except Exception:
            logger.warning(
                "Revoke failed during stop of run %s; daemon exits via "
                "status poll", run_id, exc_info=True,
            )
        logger.info("Streaming run %s stopped by operator", run_id)
        return True, None
    finally:
        if owned:
            session.close()


def request_restart(run_id, user, db=None) -> tuple:
    """Transition STREAMING_STOPPED/CANCELLED/FAILED -> STREAMING_ACTIVE (M-078, R2-001).

    Dispatches a fresh daemon, then atomically claims ACTIVE with the new
    task id. The state gate is checked *before* dispatching so a refused
    restart never launches an orphan daemon; if the claim loses a race
    after dispatch, the orphan is revoked immediately (the daemon's own
    single-flight claim would also refuse it).
    """
    session, owned = _transition_session(db)
    try:
        run, run_uuid, error = _load_transition_run(session, run_id)
        if error is not None:
            return False, error
        denial = _check_streaming_access(run, user)
        if denial is not None:
            return False, denial
        if run.status not in _RESTART_FROM:
            return False, _transition_conflict(
                session, run_uuid, run_id, "restart", "STREAMING_STOPPED, CANCELLED or FAILED")
        try:
            dispatched = run_streaming_pipeline.delay(str(run_uuid))
        except Exception as exc:
            return False, f"failed to dispatch daemon for run {run_id}: {exc}"
        new_task_id = getattr(dispatched, "id", None)
        if not _atomic_transition(
            session, run_uuid, _RESTART_FROM,
            PipelineStatus.STREAMING_ACTIVE,
            {"celery_task_id": new_task_id},
        ):
            try:
                celery_app.control.revoke(
                    new_task_id, terminate=True, signal="SIGTERM")
            except Exception:
                logger.warning(
                    "Revoke of orphan daemon %s failed", new_task_id,
                    exc_info=True,
                )
            return False, _transition_conflict(
                session, run_uuid, run_id, "restart", "STREAMING_STOPPED, CANCELLED or FAILED")
        logger.info(
            "Streaming run %s restarted by operator (task %s)",
            run_id, new_task_id,
        )
        return True, None
    finally:
        if owned:
            session.close()


def _settle_final_status(db, run_id):
    """Persist the daemon's final status on loop exit (M-078).

    Daemon-owned states (ACTIVE/PAUSED) settle to STREAMING_STOPPED;
    anything else (FAILED with a reason, CANCELLED, ...) is preserved so
    loop exit never clobbers a terminal status. Returns the final status,
    or None when the run no longer exists.
    """
    run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    if run is None:
        return None
    if run.status in (
        PipelineStatus.STREAMING_ACTIVE, PipelineStatus.STREAMING_PAUSED,
    ):
        run.status = PipelineStatus.STREAMING_STOPPED
        db.commit()
        return PipelineStatus.STREAMING_STOPPED
    return run.status


def _claim_run(db, run_id: str, task_id: str | None) -> str | None:
    """Atomically claim a run for this daemon (M-042 single-flight).

    Returns None when this task owns the run, else a human-readable
    reason so the caller can exit without touching run state. The claim
    is a conditional UPDATE (check-and-set): concurrent duplicate
    dispatches serialize on the row and all but one lose.
    """
    try:
        run_uuid = run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(str(run_id))
    except (ValueError, AttributeError, TypeError):
        return f"Run not found: {run_id}"
    run = db.query(PipelineRun).filter(PipelineRun.id == run_uuid).first()
    if run is None:
        return f"Run not found: {run_id}"

    if run.status in (
        PipelineStatus.PENDING, PipelineStatus.STREAMING_STOPPED,
    ):
        expected = run.status
        updated = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.id == run_uuid,
                PipelineRun.status == expected,
            )
            .update(
                {
                    "status": PipelineStatus.STREAMING_ACTIVE,
                    "celery_task_id": task_id,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if updated == 1:
            return None
        db.expire_all()
        run = db.query(PipelineRun).filter(
            PipelineRun.id == run_uuid).first()
        if run is None:
            return f"Run not found: {run_id}"

    if run.status == PipelineStatus.STREAMING_ACTIVE:
        if run.celery_task_id in (None, task_id):
            # Restart path (endpoint sets ACTIVE + task id before the
            # delay lands) or adopting an unclaimed ACTIVE row.
            updated = (
                db.query(PipelineRun)
                .filter(
                    PipelineRun.id == run_uuid,
                    PipelineRun.status == PipelineStatus.STREAMING_ACTIVE,
                    (
                        PipelineRun.celery_task_id.is_(None)
                        if task_id is None
                        else (
                            (PipelineRun.celery_task_id.is_(None))
                            | (PipelineRun.celery_task_id == task_id)
                        )
                    ),
                )
                .update(
                    {"celery_task_id": task_id},
                    synchronize_session=False,
                )
            )
            db.commit()
            if updated == 1:
                return None
            return (
                f"concurrent duplicate dispatch for run {run_id}; exiting"
            )
        return (
            f"duplicate daemon for run {run_id} (owned by task "
            f"{run.celery_task_id}); exiting"
        )
    return (
        f"run {run_id} is {run.status.value}, not dispatchable; exiting"
    )


# Batch-only step types the streaming daemon cannot execute (M-160).
_BATCH_ONLY_STEP_TYPES = frozenset({"load", "save"})


def _validate_streaming_steps(steps) -> str | None:
    """Reject batch-only steps explicitly instead of silently dropping them.

    The daemon used to filter ``load``/``save`` steps out of the batch
    without a word, so pipelines ran with missing sinks/sources. Fail
    fast with a clear reason instead. Returns the error, or None when
    every step is streamable.
    """
    offenders = []
    for step in (steps or []):
        step_kind = _step_type(step)
        if step_kind in _BATCH_ONLY_STEP_TYPES:
            step_name = getattr(step, "name", None) or step_kind or "<unnamed>"
            offenders.append(f"{step_name} ({step_kind})")
    if not offenders:
        return None
    return (
        "pipeline contains batch-only steps that streaming cannot execute "
        f"({', '.join(offenders)}); remove them or "
        "run the pipeline as a batch"
    )


def check_topic_access(db, topic, user, *, action="publish") -> tuple:
    """Ownership check for streaming topics (M-006).

    Returns ``(allowed, reason)``: ``(True, None)`` when ``user`` may use
    ``topic`` for ``action`` (``"publish"`` or ``"consume"``), otherwise
    ``(False, why)``. This is the daemon-side implementation of the
    streaming control-plane ``check_topic_access`` contract:

    * malformed topic names are denied (no wildcard/probe games);
    * admins bypass ownership;
    * a topic with no catalog owner is open (first-use provisioning —
      the catalog registers ownership from lineage on first run);
    * otherwise only the recorded owner (or an admin) may use it.

    ``user`` may be a User row (``.id``/``.role``) or a bare id; the role
    is resolved from the DB when only an id is given.
    """
    name = topic.strip() if isinstance(topic, str) else topic
    if not name:
        return False, f"no {action} topic configured"
    try:
        validate_topic_name(name)
    except ValueError as exc:
        return False, f"invalid {action} topic '{topic}': {exc}"
    actor_id = getattr(user, "id", user)
    role = getattr(user, "role", None)
    if role is None and actor_id is not None:
        actor_uuid = _coerce_run_uuid(actor_id)
        account = (
            db.query(User).filter(User.id == actor_uuid).first()
            if actor_uuid is not None else None
        )
        role = getattr(account, "role", None)
    if role == "admin":
        return True, None
    assets = (
        db.query(DataAsset)
        .filter(DataAsset.asset_type == "topic", DataAsset.name == name)
        .all()
    )
    owners = {str(a.owner_id) for a in assets if a.owner_id is not None}
    if not owners:
        return True, None
    actor_uuid = (
        _coerce_run_uuid(actor_id) if actor_id is not None else None
    )
    if actor_uuid is not None and str(actor_uuid) in owners:
        return True, None
    return False, (
        f"{action} topic '{name}' is owned by another user; "
        "request access from the topic owner"
    )


def _require_topic_access(db, pipeline_run, topics) -> str | None:
    """Enforce topic ownership for the daemon's topics (M-006).

    ``topics`` maps a role (``"consume"``/``"publish"``) to a topic name
    or None (a pipeline may have no publish step). Returns an error
    message, or None when every required check passes. The actor is the
    run owner: daemons act with exactly the owner's authority, never more.
    """
    for role, name in topics.items():
        if not name:
            continue
        allowed, reason = check_topic_access(
            db, name, pipeline_run.user_id, action=role)
        if not allowed:
            return (
                f"run owner lacks {role} access to topic '{name}': {reason}"
            )
    return None


def _daemon_group_id(run_id, explicit: str | None) -> str:
    """Consumer group for a daemon run (M-131).

    Defaults to the full run id: the old 8-char prefix could collide
    across runs, silently sharing offsets between unrelated daemons.
    """
    if explicit:
        return explicit
    return f"piq-{run_id}"


def _daemon_consumer(consumer_group: str, auto_offset_reset: str | None = "earliest"):
    """Build the daemon's Kafka consumer (M-131).

    The daemon mints a fresh group id per run, and the broker default
    (``auto_offset_reset="latest"``) would silently skip whatever backlog
    is already waiting on the topic. ``"earliest"`` replays it instead;
    committed offsets still resume where the group left off, so earliest
    only affects first contact with uncommitted partitions. The choice is
    explicit per pipeline (``auto_offset_reset`` on the consume step) and
    anything unrecognised falls back to earliest — never silently skip.
    """
    reset = (auto_offset_reset or "earliest").strip().lower()
    if reset not in ("earliest", "latest"):
        logger.warning(
            "Unknown auto_offset_reset '%s' for group '%s'; "
            "falling back to 'earliest'",
            auto_offset_reset, consumer_group,
        )
        reset = "earliest"
    logger.info(
        "Consumer group '%s' starting with auto_offset_reset='%s' (%s)",
        consumer_group, reset,
        "replaying backlog" if reset == "earliest"
        else "processing only new messages",
    )
    return make_consumer(consumer_group, auto_offset_reset=reset)


def _register_topic_assets(db, owner_id, topics) -> None:
    """Record first-use topic ownership in the catalog (M-006).

    The ownership gate (:func:`check_topic_access`) reads ``topic``
    DataAssets; without registration the gate would stay fail-open
    forever. Best-effort: a catalog failure rolls back and warns (the
    next daemon startup retries) instead of blocking the run.
    """
    names = {t.strip() for t in (topics or []) if t and t.strip()}
    if not names or owner_id is None:
        return
    try:
        from backend.repositories.catalog import NS_REDPANDA, upsert_data_asset
        for name in sorted(names):
            upsert_data_asset(
                db=db,
                asset_type="topic",
                name=name,
                namespace=NS_REDPANDA,
                owner_id=owner_id,
                metadata={"provisioned_by": "streaming-daemon"},
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning(
            "Topic ownership registration failed; "
            "the next startup will retry",
            exc_info=True,
        )


def _apply_backpressure(consumer, db, run_id) -> bool:
    """Pause fetching while consumer lag is over threshold (M-131).

    Returns True when the caller must skip fetching this iteration (the
    backlog is left on the broker for the next poll — never skipped).
    The cooldown sleep is interruptible so stop/revoke wakes promptly,
    and a backpressure SSE event is emitted so the stall is visible
    instead of silent.
    """
    lag = _compute_consumer_lag(consumer)
    if lag <= BACKPRESSURE_LAG_THRESHOLD:
        return False
    logger.warning(
        "BACKPRESSURE: consumer_lag=%d (threshold=%d), pausing fetch, "
        "cooling down %ds",
        lag, BACKPRESSURE_LAG_THRESHOLD, BACKPRESSURE_COOLDOWN_SEC,
    )
    _sse_backpressure(run_id, lag)
    _sleep_interruptible(BACKPRESSURE_COOLDOWN_SEC, db, run_id)
    return True


def _sse_backpressure(run_id: str, lag: int) -> None:
    """Emit a visible backpressure event on the run's progress channel."""
    try:
        redis = get_pubsub_redis()
        channel = f"pipeline_progress:{run_id}"
        payload = {
            "event": "streaming_backpressure",
            "run_id": str(run_id),
            "consumer_lag": lag,
            "threshold": BACKPRESSURE_LAG_THRESHOLD,
        }
        redis.publish(channel, orjson.dumps(sign_sse_payload(payload)))
    except Exception:
        pass


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


def _commit_offsets_guarded(consumer, messages, *, attempts: int = 3) -> None:
    """Commit offsets with bounded retries (R2-001).

    A transient broker blip must not kill the daemon or poison the DLQ
    with an already-published batch. Retries with 1s/2s/4s backoff; on
    persistent failure raises StreamingCommitError so the caller can
    stop the daemon WITHOUT DLQ-ing healthy data. Redelivery after a
    restart may duplicate already-published rows (at-least-once).
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            _commit_offsets(consumer, messages)
        except Exception as exc:
            last = exc
            logger.warning(
                "Offset commit attempt %d/%d failed; backing off",
                attempt + 1, attempts)
            time.sleep(min(2 ** attempt, 4))
        else:
            return
    raise StreamingCommitError(
        f"offset commit failed after {attempts} attempts: {last}") from last


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
        _commit_offsets_guarded(consumer, messages)
    except (StreamingPublishError, StreamingDLQError):
        logger.exception(
            "Batch persist failed; withholding offset commit for redelivery")
        stats["failed"] += len(messages)
        return consecutive_errors + 1
    except StreamingCommitError:
        # R2-001: the batch was already published; DLQ-ing it would poison
        # the DLQ with healthy data AND the throw below stops the daemon.
        # Backoff already happened inside the guarded commit; surface the
        # failure so the daemon loop marks FAILED honestly (restartable),
        # without touching the DLQ.
        raise
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
        _commit_offsets_guarded(consumer, messages)
        # R-R-002: the batch is preserved in the DLQ *and* its offsets are
        # committed, so the daemon made progress — do NOT count this toward
        # the shutdown counter. Only uncommitted (redeliverable) failures
        # wedge the loop; counting quarantined batches would shut down a
        # healthy-data pipeline on a poison topic.
        return consecutive_errors
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


def _is_transient_db_error(exc: BaseException) -> bool:
    """True for DB failures that may clear on retry (M-044).

    Connection resets, deadlocks, pool timeouts and other DBAPI-level
    failures must not kill the daemon: the run is still alive, only the
    status read failed. Anything else (programming errors, bad SQL)
    fails safe by stopping.
    """
    return isinstance(exc, (DBAPIError, SQLAlchemyTimeoutError))


def _rollback_quietly(db) -> None:
    """Best-effort rollback after a failed read; never raises."""
    with contextlib.suppress(Exception):
        db.rollback()


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
    """Check if the streaming pipeline should stop (replaces deprecated self.is_aborted()).

    A transient DB failure (connection reset, pool timeout, deadlock)
    keeps the daemon alive: only the status read failed, the run itself
    is unaffected (M-044). The session is rolled back so the next
    iteration reads cleanly. Any other error fails safe by stopping.
    """
    try:
        db.expire_all()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    except Exception as exc:
        if _is_transient_db_error(exc):
            _rollback_quietly(db)
            logger.warning(
                "Transient DB error reading run %s status; "
                "daemon keeps running",
                run_id, exc_info=True,
            )
            return False
        logger.exception(
            "Status read failed for run %s; stopping daemon", run_id)
        return True
    if not run:
        return True
    return run.status not in (
        PipelineStatus.STREAMING_ACTIVE,
        PipelineStatus.STREAMING_PAUSED,
    )


def _handle_pause(db, run_id: str) -> bool:
    """Block while the run is paused; True means the daemon should stop.

    The wait is abort-interruptible (stop/revoke wakes it within ~1s) so
    a paused daemon stays cancellable (M-152). Transient DB errors during
    the wait are swallowed with a rollback: the daemon keeps waiting
    instead of exiting (M-044).
    """
    try:
        db.expire_all()
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
    except Exception as exc:
        if _is_transient_db_error(exc):
            _rollback_quietly(db)
            logger.warning(
                "Transient DB error reading run %s status; continuing",
                run_id, exc_info=True,
            )
            return False
        logger.exception(
            "Status read failed for run %s; stopping daemon", run_id)
        return True
    if not run:
        return True
    if run.status != PipelineStatus.STREAMING_PAUSED:
        return False
    logger.info("Paused — waiting for resume signal")
    while True:
        if _sleep_interruptible(_PAUSE_POLL_SEC, db, run_id):
            return True
        try:
            db.expire_all()
            run = db.query(PipelineRun).filter(
                PipelineRun.id == run_id).first()
        except Exception as exc:
            if _is_transient_db_error(exc):
                _rollback_quietly(db)
                logger.warning(
                    "Transient DB error while paused (run %s); "
                    "keep waiting",
                    run_id, exc_info=True,
                )
                continue
            logger.exception(
                "Status read failed for run %s; stopping daemon", run_id)
            return True
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
