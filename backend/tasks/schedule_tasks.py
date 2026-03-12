"""Celery Beat task for checking and triggering pipeline schedules.

Runs every minute via Celery Beat, finds active schedules whose
next_run_at <= now, and dispatches execute_pipeline_task for each.
"""

import logging
from datetime import datetime, timezone

from croniter import croniter

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.models import PipelineRun, PipelineSchedule, PipelineStatus

logger = logging.getLogger(__name__)


@celery_app.task(name="schedules.check", ignore_result=True)
def check_schedules() -> dict:
    """Find due schedules and trigger pipeline execution for each."""
    db = SessionLocal()
    triggered = 0
    try:
        now = datetime.now(timezone.utc)
        due_schedules = (
            db.query(PipelineSchedule)
            .filter(
                PipelineSchedule.is_active == True,  # noqa: E712
                PipelineSchedule.next_run_at <= now,
            )
            .all()
        )

        for schedule in due_schedules:
            try:
                # Create a PipelineRun record
                pipeline_run = PipelineRun(
                    name=schedule.pipeline_name,
                    status=PipelineStatus.PENDING,
                    yaml_config=schedule.yaml_config,
                    user_id=schedule.user_id,
                )
                db.add(pipeline_run)
                db.commit()
                db.refresh(pipeline_run)

                # Dispatch execution task
                from backend.tasks.pipeline_tasks import execute_pipeline_task
                execute_pipeline_task.delay(str(pipeline_run.id))

                # Update schedule timestamps
                schedule.last_run_at = now
                cron = croniter(schedule.cron_expression, now)
                schedule.next_run_at = cron.get_next(datetime).replace(tzinfo=timezone.utc)
                db.commit()

                triggered += 1
                logger.info(
                    "Schedule %s triggered pipeline run %s",
                    schedule.id, pipeline_run.id,
                )
            except Exception as exc:
                logger.error("Failed to trigger schedule %s: %s", schedule.id, exc)
                db.rollback()

    except Exception as exc:
        logger.error("Error checking schedules: %s", exc)
    finally:
        db.close()

    return {"triggered": triggered}
