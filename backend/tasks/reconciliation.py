"""Reconcile runs wedged in non-terminal states (S01, S14).

A worker killed mid-run (SIGKILL, OOM, node loss) can leave a run in
PENDING/RUNNING/HEALING forever: Celery redelivers the task, but the
redelivery no-ops on non-PENDING runs, and nothing ever writes a
terminal status. This reconciler finds stale non-terminal runs whose
Celery task is gone (terminal result state, revoked, or unknown) and
moves them to TIMEOUT with a terminal SSE event so the API, the UI and
monitoring converge instead of hanging forever.

Runs whose task still looks alive (PENDING/STARTED/RETRY in the result
backend) are left alone: a long legitimate run must never be reaped.
Streaming runs are daemons by design and are never touched.
"""

import logging
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
# A task that looks alive in the result backend is trusted, even if old.
LIVE_TASK_STATES = frozenset({"PENDING", "STARTED", "RETRY"})


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


def reconcile_stuck_runs(
    db=None,
    timeout: timedelta = timedelta(minutes=30),
) -> dict:
    """Mark stale non-terminal runs TIMEOUT when their task is gone.

    Returns counts so the beat logs stay informative.
    """
    from backend.services.audit_service import log_action

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
            run.status = PipelineStatus.TIMEOUT
            run.completed_at = utcnow()
            run.error_message = (
                "Run timed out: worker stopped reporting progress "
                f"(last celery task state: {task_state or 'unknown'})"
            )
            db.commit()
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
