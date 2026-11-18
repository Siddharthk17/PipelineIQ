"""Celery Beat task for checking and triggering pipeline schedules.

Runs every minute via Celery Beat, finds active schedules whose
next_run_at <= now, and dispatches execute_pipeline_task for each.
Also creates ScheduleRun records and updates schedule statistics.
"""

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

    return {"triggered": triggered, "locked": False}
