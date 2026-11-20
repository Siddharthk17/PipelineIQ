"""Reconcile runs wedged in non-terminal states (S01, S14).

A worker killed mid-run (SIGKILL, OOM, node loss) can leave a run in
PENDING/RUNNING/HEALING forever: Celery redelivers the task, but the
redelivery no-ops on non-PENDING runs, and nothing ever writes a
terminal status. This reconciler finds stale non-terminal runs whose
Celery task is gone (terminal result state, revoked, or unknown) and
moves them to TIMEOUT with a terminal SSE event so the API, the UI and
monitoring converge instead of hanging forever.

Celery result-backend ``PENDING`` is ambiguous: ``AsyncResult.state``
reports ``PENDING`` both for a task that is genuinely queued and for a
task id the backend has never seen or has already expired. A stale run
whose task reads ``PENDING`` is therefore only trusted when the broker
still reports the task as active/reserved/scheduled; otherwise the
worker is treated as dead and the run is reaped.

Runs with no ``celery_task_id`` at all are broker-outage orphans
(M-020): the run row committed but the ``.delay()`` never reached the
broker. Once stale, they converge to TIMEOUT here instead of sitting in
PENDING forever.

A Redis single-flight lock ensures concurrent beat replicas do not
double-reap the same runs.
"""

import logging
import uuid
from datetime import timedelta

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.models import PipelineRun, PipelineStatus
from backend.utils.time_utils import utcnow

logger = logging.getLogger(__name__)

RECONCILABLE_STATUSES = (
    PipelineStatus.PENDING,
    PipelineStatus.RUNNING,
    PipelineStatus.HEALING,
)
# Result-backend states that prove a worker is actively holding the task.
# PENDING is deliberately excluded: AsyncResult defaults to PENDING for
# unknown/expired task ids, so trusting it reaps nothing (M-018).
LIVE_TASK_STATES = frozenset({"STARTED", "RETRY"})
# Ambiguous result-backend state: queued-live or dead-expired. Resolved
# via a broker inspect probe (see _is_task_known_to_workers).
AMBIGUOUS_TASK_STATES = frozenset({"PENDING"})

RECONCILE_LOCK_KEY = "locks:reconcile:stuck_runs"
RECONCILE_LOCK_TTL_SECONDS = 300


def _task_state(celery_task_id: str | None) -> str | None:
    """Best-effort Celery state lookup; None means unknown, not dead."""
    if not celery_task_id:
        return None
    try:
        from celery.result import AsyncResult

        return AsyncResult(celery_task_id, app=celery_app).state
    except Exception as exc:
        logger.warning(
            "Could not inspect Celery task %s: %s", celery_task_id, exc)
        return None


def _is_task_known_to_workers(celery_task_id: str) -> bool | None:
    """Check whether broker/workers still hold a task id.

    Returns True when a worker reports the task as active, reserved or
    scheduled; False when the inspect succeeds and no worker knows it;
    None when the broker cannot be reached (indeterminate — caller must
    fail safe towards leaving the run alone).
    """
    try:
        inspect = celery_app.control.inspect()
        if inspect is None:
            return None
        answered = False
        for probe in ("active", "reserved", "scheduled"):
            try:
                data = getattr(inspect, probe)()
            except Exception:
                continue
            if data is None:
                # No worker replied to this probe; another probe may
                # still give a definitive answer.
                continue
            answered = True
            for tasks in data.values():
                for task in tasks or []:
                    if isinstance(task, dict) and task.get("id") == celery_task_id:
                        return True
    except Exception as exc:
        logger.warning(
            "Could not probe workers for task %s: %s", celery_task_id, exc)
        return None
    else:
        # Inspect answered but nobody holds the task.
        return False if answered else None


def _acquire_reconcile_lock() -> tuple[bool, str]:
    """Single-flight guard so beat replicas never double-reap.

    Returns (acquired, token). When Redis is unavailable the reconciler
    proceeds unlocked (availability over mutual exclusion) and logs.
    """
    token = uuid.uuid4().hex
    try:
        from backend.db.redis_pools import get_cache_redis

        redis_client = get_cache_redis()
        acquired = bool(
            redis_client.set(
                RECONCILE_LOCK_KEY, token,
                nx=True, ex=RECONCILE_LOCK_TTL_SECONDS,
            )
        )
        if not acquired:
            logger.info(
                "Reconciler skipped; another replica holds the lock")
    except Exception as exc:
        logger.warning(
            "Reconcile lock unavailable; proceeding unlocked: %s", exc)
        return True, ""
    else:
        return acquired, token


def _release_reconcile_lock(token: str) -> None:
    """Release the single-flight lock only if we still own it."""
    if not token:
        return
    try:
        from backend.db.redis_pools import get_cache_redis

        redis_client = get_cache_redis()
        current = redis_client.get(RECONCILE_LOCK_KEY)
        if isinstance(current, bytes):
            current = current.decode("utf-8")
        if current == token:
            redis_client.delete(RECONCILE_LOCK_KEY)
    except Exception:
        logger.warning(
            "Failed to release reconcile lock", exc_info=True)


def _run_age(run: PipelineRun, now) -> timedelta | None:
    started = run.started_at or run.created_at
    if started is None:
        return None
    return now.replace(tzinfo=None) - started.replace(tzinfo=None)


def reconcile_stuck_runs(
    db=None,
    timeout: timedelta = timedelta(minutes=30),
) -> dict:
    """Mark stale non-terminal runs TIMEOUT when their task is gone.

    Returns counts so the beat logs stay informative.
    """
    from backend.services.audit_service import log_action

    locked, lock_token = _acquire_reconcile_lock()
    if not locked:
        return {"checked": 0, "timed_out": 0}
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        cutoff = utcnow() - timeout
        candidates = (
            db.query(PipelineRun)
            .filter(
                PipelineRun.status.in_(RECONCILABLE_STATUSES),
            )
            .all()
        )
        try:
            from backend.tasks.pipeline_tasks import _publish_terminal_event

            publish_terminal = _publish_terminal_event
        except Exception:
            publish_terminal = None

        checked = 0
        timed_out = 0
        for run in candidates:
            started = run.started_at or run.created_at
            if started is not None and started.replace(tzinfo=None) >= cutoff.replace(tzinfo=None):
                continue
            checked += 1
            task_state = _task_state(run.celery_task_id)
            if task_state in LIVE_TASK_STATES:
                continue
            if task_state in AMBIGUOUS_TASK_STATES and run.celery_task_id:
                # PENDING from the result backend alone proves nothing.
                # Only a broker sighting keeps the run alive.
                worker_knowledge = _is_task_known_to_workers(
                    run.celery_task_id)
                if worker_knowledge is True:
                    continue
                if worker_knowledge is None:
                    # Broker unreachable: a merely-stale queued task must
                    # not be reaped, so require double the timeout before
                    # treating it as dead.
                    age = _run_age(run, utcnow())
                    if age is None or age < 2 * timeout:
                        continue
            if not run.celery_task_id:
                # M-020 orphan: run row committed but dispatch never
                # reached the broker. Converge to TIMEOUT here instead
                # of sitting in PENDING forever.
                error_message = (
                    "Run timed out: dispatch never reached the broker "
                    "(no celery task id; orphaned PENDING run)"
                )
            else:
                error_message = (
                    "Run timed out: worker stopped reporting progress "
                    f"(last celery task state: {task_state or 'unknown'})"
                )
            run.status = PipelineStatus.TIMEOUT
            run.completed_at = utcnow()
            run.error_message = error_message
            try:
                db.commit()
            except Exception:
                # One poisoned commit must not wedge the whole sweep
                # (M-173): roll back and continue with the remaining runs.
                db.rollback()
                logger.warning(
                    "Failed to mark stale run %s TIMEOUT; continuing",
                    run.id, exc_info=True)
                continue
            timed_out += 1
            logger.warning(
                "Marked stale run %s TIMEOUT (task state: %s)",
                run.id, task_state or "unknown")
            if publish_terminal is not None:
                try:
                    publish_terminal(
                        str(run.id), "pipeline_failed",
                        PipelineStatus.TIMEOUT.value, run.error_message or "",
                    )
                except Exception:
                    logger.warning(
                        "Failed to publish timeout event for run %s",
                        run.id)
            try:
                log_action(
                    db, "pipeline_timed_out", user_id=run.user_id,
                    resource_type="pipeline_run", resource_id=run.id,
                    details={"celery_task_state": task_state or "unknown"},
                    request=None,
                )
            except Exception:
                logger.warning(
                    "Failed to audit timeout for run %s", run.id)
        return {"checked": checked, "timed_out": timed_out}
    finally:
        _release_reconcile_lock(lock_token)
        if own_session:
            db.close()


@celery_app.task(
    name="tasks.reconcile_stuck_runs",
    queue="bulk",
    ignore_result=True,
)
def reconcile_stuck_runs_task() -> dict:
    """Beat entrypoint: reap wedged runs into TIMEOUT (S01, S14)."""
    return reconcile_stuck_runs()
