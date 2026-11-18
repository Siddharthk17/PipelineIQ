"""Celery Beat task for checking and triggering pipeline schedules.

Runs every minute via Celery Beat, finds active schedules whose
next_run_at <= now, and dispatches execute_pipeline_task for each.
Also creates ScheduleRun records and updates schedule statistics.
"""

import contextlib
import logging
import uuid
from datetime import UTC, datetime

from croniter import croniter
from sqlalchemy import or_

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.db.redis_pools import get_cache_redis
from backend.models import PipelineRun, PipelineSchedule, PipelineStatus, ScheduleRun

logger = logging.getLogger(__name__)

SCHEDULE_CHECK_LOCK_KEY = "locks:schedules:check"
SCHEDULE_CHECK_LOCK_TTL_SECONDS = 110
SCHEDULE_CHECK_BATCH_LIMIT = 100

# ScheduleRun statuses the checker writes; rows still in one of these are
# "open" and eligible for outcome sync (M-044).
SCHEDULE_RUN_OPEN_STATUSES = ("QUEUED", "DISPATCHED", "RUNNING")

# Terminal pipeline outcomes mirrored into schedule_runs (M-044).
_TERMINAL_RUN_TO_SCHEDULE_STATUS = {
    PipelineStatus.COMPLETED: "COMPLETED",
    PipelineStatus.FAILED: "FAILED",
    PipelineStatus.CANCELLED: "CANCELLED",
    PipelineStatus.TIMEOUT: "TIMEOUT",
    PipelineStatus.CONTRACT_VIOLATION: "CONTRACT_VIOLATION",
    PipelineStatus.HEALED: "HEALED",
}


@celery_app.task(name="schedules.check", ignore_result=True)
def check_schedules() -> dict:
    """Find due schedules and trigger pipeline execution for each."""
    lock_token = str(uuid.uuid4())
    lock_acquired = False
    try:
        redis = get_cache_redis()
        lock_acquired = bool(
            redis.set(
                SCHEDULE_CHECK_LOCK_KEY,
                lock_token,
                nx=True,
                ex=SCHEDULE_CHECK_LOCK_TTL_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("Schedule lock unavailable; continuing unlocked: %s", exc)

    if not lock_acquired:
        logger.info("Schedule check skipped; another worker holds the lock")
        return {"triggered": 0, "locked": True}

    db = SessionLocal()
    triggered = 0
    synced = 0
    backlog = 0
    try:
        now = datetime.now(UTC)
        # Row-level lock so overlapping beats serialize (M-071): even when
        # the Redis lock expires early or is skipped during a Redis
        # outage, two workers cannot fire the same schedule twice.
        # SQLite has a single writer and ignores FOR UPDATE.
        due_query = (
            db.query(PipelineSchedule)
            .filter(
                PipelineSchedule.is_active,
                PipelineSchedule.next_run_at <= now,
            )
            .order_by(PipelineSchedule.next_run_at.asc())
            .limit(SCHEDULE_CHECK_BATCH_LIMIT)
        )
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            due_query = due_query.with_for_update(skip_locked=True)
        due_schedules = due_query.all()

        for schedule in due_schedules:
            # Post-lock re-check: a concurrent beat may have advanced this
            # schedule while we waited on the row lock. Normalize tz: PG
            # returns aware datetimes, SQLite naive ones.
            next_due = schedule.next_run_at
            if next_due is None:
                continue
            if next_due.tzinfo is None:
                next_due = next_due.replace(tzinfo=UTC)
            if next_due > now:
                continue
            try:
                # Record this firing in schedule_runs
                schedule_run = ScheduleRun(
                    schedule_id=schedule.id,
                    triggered_at=now,
                    status="RUNNING",
                )
                db.add(schedule_run)

                # Create a PipelineRun record
                pipeline_run = PipelineRun(
                    id=uuid.uuid4(),
                    name=schedule.pipeline_name,
                    status=PipelineStatus.PENDING,
                    yaml_config=schedule.yaml_config,
                    user_id=schedule.user_id,
                    trigger="scheduled",
                    schedule_id=schedule.id,
                )
                db.add(pipeline_run)

                # Advance the schedule in the SAME commit (atomic
                # check-and-set): a concurrent beat sees the new
                # next_run_at and skips this schedule, so a crash after
                # commit can never double-fire it.
                schedule_run.run_id = pipeline_run.id
                schedule.last_run_at = now
                schedule.last_run_status = "RUNNING"
                schedule.total_runs += 1
                cron = croniter(schedule.cron_expression, now)
                schedule.next_run_at = cron.get_next(
                    datetime).replace(tzinfo=UTC)
                db.commit()

                # Dispatch execution task on the 'bulk' queue. The run row
                # is committed, so the worker always finds it; a dispatch
                # failure marks the run instead of stranding PENDING (M-071).
                # The deterministic task id makes dispatch idempotent: a
                # broker redelivery of this beat reuses the same Celery
                # task id instead of queueing a duplicate run (M-071).
                try:
                    from backend.tasks.pipeline_tasks import execute_pipeline_task
                    execute_pipeline_task.apply_async(
                        args=[str(pipeline_run.id)],
                        queue="bulk",
                        task_id=f"sched-{schedule_run.id}",
                    )
                except Exception as dispatch_exc:
                    logger.exception(
                        "Dispatch failed for schedule %s run %s",
                        schedule.id, pipeline_run.id)
                    try:
                        pipeline_run.status = PipelineStatus.FAILED
                        pipeline_run.error_message = (
                            f"schedule dispatch failed: {dispatch_exc}"[:2000]
                        )
                        schedule_run.status = "FAILED"
                        schedule_run.error_message = (
                            f"schedule dispatch failed: {dispatch_exc}"[:2000]
                        )
                        db.commit()
                    except Exception:
                        db.rollback()
                        logger.exception(
                            "Failed to record dispatch failure for run %s",
                            pipeline_run.id)
                    continue

                triggered += 1
                logger.info(
                    "Schedule %s triggered pipeline run %s",
                    schedule.id, pipeline_run.id,
                )
            except Exception:
                logger.exception(
                    "Failed to trigger schedule %s",
                    schedule.id)
                db.rollback()

        # Mirror terminal pipeline outcomes into open schedule_runs so
        # dispatched-but-never-completed rows cannot strand as RUNNING
        # forever and counters converge (M-044). A sync failure must not
        # fail the tick: the next beat retries.
        try:
            synced = _sync_open_schedule_runs(db)
        except Exception:
            db.rollback()
            logger.exception("Error syncing schedule runs")
            synced = 0

        # Backlog (M-131): the batch limit bounds one tick's work, but due
        # schedules beyond it must be reported, never silently skipped.
        try:
            backlog = (
                db.query(PipelineSchedule)
                .filter(
                    PipelineSchedule.is_active,
                    PipelineSchedule.next_run_at <= now,
                )
                .count()
            )
        except Exception:
            db.rollback()
            logger.exception("Error counting schedule backlog")
            backlog = 0
        if backlog:
            logger.warning(
                "Scheduler backlog: %d due schedule(s) left unprocessed "
                "(batch limit %d)",
                backlog, SCHEDULE_CHECK_BATCH_LIMIT,
            )

    except Exception:
        logger.exception("Error checking schedules")
    finally:
        try:
            redis = get_cache_redis()
            current_token = redis.get(SCHEDULE_CHECK_LOCK_KEY)
            if isinstance(current_token, bytes):
                current_token = current_token.decode("utf-8")
            if current_token == lock_token:
                redis.delete(SCHEDULE_CHECK_LOCK_KEY)
        except Exception:
            logger.debug("Schedule lock release skipped", exc_info=True)
        db.close()

    return {
        "triggered": triggered,
        "locked": False,
        "backlog": backlog,
        "synced": synced,
    }


def _sync_open_schedule_runs(db, limit: int = SCHEDULE_CHECK_BATCH_LIMIT) -> int:
    """Mirror terminal pipeline outcomes into open schedule_runs (M-044).

    Each open schedule_run whose linked pipeline_run reached a terminal
    state records the outcome (status, error, duration) and bumps its
    schedule's counters exactly once — the row leaves the open set, so
    re-syncs are no-ops. Rows whose pipeline run vanished are marked
    FAILED with a reason instead of stranding. Per-run isolation: one bad
    row rolls back alone and the sync continues.
    """
    synced = 0
    open_runs = (
        db.query(ScheduleRun)
        .filter(
            ScheduleRun.run_id.is_not(None),
            or_(
                ScheduleRun.status.is_(None),
                ScheduleRun.status.in_(SCHEDULE_RUN_OPEN_STATUSES),
            ),
        )
        .order_by(ScheduleRun.triggered_at.asc())
        .limit(limit)
        .all()
    )
    for schedule_run in open_runs:
        try:
            pipeline_run = (
                db.query(PipelineRun)
                .filter(PipelineRun.id == schedule_run.run_id)
                .first()
            )
            if pipeline_run is None:
                schedule_run.status = "FAILED"
                schedule_run.error_message = (
                    "linked pipeline run is missing; cannot determine outcome"
                )
            else:
                terminal = _TERMINAL_RUN_TO_SCHEDULE_STATUS.get(
                    pipeline_run.status)
                if terminal is None:
                    continue  # still in flight; leave open
                schedule_run.status = terminal
                if pipeline_run.error_message:
                    schedule_run.error_message = pipeline_run.error_message[:2000]
                started = pipeline_run.started_at
                completed = pipeline_run.completed_at
                if started is not None and completed is not None:
                    with contextlib.suppress(Exception):
                        schedule_run.duration_seconds = int(
                            (completed - started).total_seconds())
            schedule = (
                db.query(PipelineSchedule)
                .filter(PipelineSchedule.id == schedule_run.schedule_id)
                .first()
            )
            if schedule is not None:
                schedule.last_run_status = schedule_run.status
                if schedule_run.status == "COMPLETED":
                    schedule.successful_runs += 1
                elif schedule_run.status == "FAILED":
                    schedule.failed_runs += 1
                elif schedule_run.status == "HEALED":
                    schedule.healed_runs += 1
            db.commit()
            synced += 1
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to sync schedule run %s", schedule_run.id)
    return synced
