"""
Celery task that fires when a schedule's cron expression triggers.
Creates a new pipeline run and submits it for execution.
"""
import logging
from datetime import UTC, datetime
from uuid import uuid4

from backend.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.execute_scheduled_pipeline",
    queue="bulk",            # scheduled runs go to bulk — they are not time-critical
    bind=True,
    max_retries=1,           # if creating the run fails, try once more
    default_retry_delay=60,  # wait 60s before retry
    soft_time_limit=600,     # 10 minutes: SIGTERM
    time_limit=660,          # 11 minutes: SIGKILL
)
def execute_scheduled_pipeline(self, schedule_id: str) -> dict:
    """
    Execute a pipeline on behalf of a schedule.

    Called by Celery Beat when the schedule's cron fires.
    Creates a pipeline_run record and submits it for execution.

    Args:
        schedule_id: UUID of the pipeline_schedule that triggered this execution

    Returns:
        dict with run_id and status
    """
    from backend.database import SessionLocal
    from backend.models import PipelineRun, PipelineSchedule, PipelineStatus, ScheduleRun

    logger.info(f"Scheduled pipeline trigger: schedule_id={schedule_id}")

    # Use sync session since Celery tasks are typically sync
    db = SessionLocal()
    try:
        # Get the schedule
        schedule = db.query(PipelineSchedule).filter(
            PipelineSchedule.id == schedule_id).first()

        if not schedule:
            logger.error(f"Schedule not found: {schedule_id}")
            return {"error": "Schedule not found"}

        if not schedule.is_active:
            logger.info(f"Schedule {schedule_id} is inactive — skipping")
            return {"status": "skipped", "reason": "schedule_inactive"}

        # Create a schedule_run record (tracks this firing)
        from backend.utils.uuid_utils import as_uuid

        schedule_uuid = as_uuid(schedule_id)
        schedule_run = ScheduleRun(
            schedule_id=schedule_uuid,
            triggered_at=datetime.now(UTC),
        )
        db.add(schedule_run)

        # Update the schedule's stats
        schedule.last_run_at = datetime.now(UTC)
        schedule.total_runs += 1
        # Note: last_run_status is updated when the run actually completes.
        # We update it here to 'RUNNING' or similar if we want, but normally
        # it's the final status.

        # Submit the pipeline for execution
        from backend.tasks.pipeline_tasks import execute_pipeline_task

        run_id = uuid4()

        # Create a pipeline_run record
        pipeline_run = PipelineRun(
            id=run_id,
            name=schedule.pipeline_name,
            status=PipelineStatus.PENDING,
            yaml_config=schedule.yaml_config,
            user_id=schedule.user_id,
            trigger="scheduled",
            schedule_id=schedule_uuid,
        )
        db.add(pipeline_run)
        db.commit()
        db.refresh(pipeline_run)

        # Update schedule_run with the run_id
        schedule_run.run_id = run_id
        db.commit()

        # Submit for asynchronous execution
        execute_pipeline_task.apply_async(
            args=[str(run_id)],
            queue="bulk",
        )

        logger.info(
            f"Scheduled pipeline submitted: schedule_id={schedule_id}, "
            f"run_id={run_id}"
        )

    except Exception as exc:
        logger.error(
            f"Failed to execute scheduled pipeline {schedule_id}: {exc}",
            exc_info=True)
        db.rollback()
        raise self.retry(exc=exc) from exc
    else:
        return {"run_id": str(run_id), "status": "submitted"}
    finally:
        db.close()
