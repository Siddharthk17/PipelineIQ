"""Celery tasks for asynchronous pipeline execution.

This is a thin orchestration layer. The task does NOT contain business
logic — it loads data from the DB, creates a progress callback that
publishes to Redis, calls PipelineRunner.execute(), and persists
results back to the DB.

Task idempotency: if the same run_id is submitted twice, the task
checks the current status and only proceeds if PENDING.
"""

# Standard library
import json
import logging
from typing import Dict

# Third-party packages
import redis

# Internal modules
from backend.celery_app import celery_app
from backend.config import settings
from backend.database import SessionLocal
from backend.models import (
    LineageGraph,
    PipelineRun,
    PipelineStatus,
    StepResult,
    StepStatus,
    UploadedFile,
)
from backend.pipeline.parser import PipelineParser
from backend.pipeline.runner import (
    PipelineRunner,
    ProgressCallback,
    StepProgressEvent,
)
from backend.pipeline.runner import PipelineStatus as RunnerPipelineStatus
from backend.pipeline.runner import StepStatus as RunnerStepStatus
from backend.utils.time_utils import utcnow

logger = logging.getLogger(__name__)


def make_redis_progress_callback(run_id: str) -> ProgressCallback:
    """Create a closure that publishes StepProgressEvent to a Redis channel.

    Channel name: f"pipeline_progress:{run_id}"
    Message format: JSON-serialized StepProgressEvent fields.

    Args:
        run_id: Unique pipeline run identifier for the channel name.

    Returns:
        A ProgressCallback that publishes events to Redis pub/sub.
    """
    redis_client = redis.Redis.from_url(settings.REDIS_URL)
    channel = f"pipeline_progress:{run_id}"

    def callback(event: StepProgressEvent) -> None:
        """Publish a progress event to Redis."""
        message = json.dumps({
            "run_id": event.run_id,
            "step_name": event.step_name,
            "step_index": event.step_index,
            "total_steps": event.total_steps,
            "status": event.status.value,
            "rows_in": event.rows_in,
            "rows_out": event.rows_out,
            "duration_ms": event.duration_ms,
            "error_message": event.error_message,
        })
        redis_client.publish(channel, message)

    return callback


def _publish_terminal_event(
    run_id: str,
    event_type: str,
    error_message: str = "",
) -> None:
    """Publish a terminal pipeline event (completed/failed) to Redis."""
    redis_client = redis.Redis.from_url(settings.REDIS_URL)
    channel = f"pipeline_progress:{run_id}"
    message = json.dumps({
        "run_id": run_id,
        "event_type": event_type,
        "error_message": error_message,
    })
    redis_client.publish(channel, message)


@celery_app.task(
    bind=True,
    name="pipeline.execute",
    max_retries=0,
    acks_late=True,
)
def execute_pipeline_task(self, run_id: str) -> Dict[str, str]:
    """Execute a pipeline run asynchronously.

    This task is idempotent: if the run_id already has a non-PENDING
    status, the task logs a warning and returns early.

    Args:
        self: Celery task instance (bound task).
        run_id: The pipeline run ID to execute.

    Returns:
        Dictionary with run_id and final status.
    """
    db = SessionLocal()
    try:
        pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()

        if pipeline_run is None:
            logger.error("Pipeline run %s not found in database", run_id)
            return {"run_id": run_id, "status": "NOT_FOUND"}

        if not _should_execute(pipeline_run):
            return {"run_id": run_id, "status": pipeline_run.status.value}

        _mark_running(db, pipeline_run)
        summary = _run_pipeline(db, pipeline_run)
        _persist_results(db, pipeline_run, summary)

        return {"run_id": run_id, "status": pipeline_run.status.value}

    except Exception as exc:
        logger.error(
            "Unexpected error executing pipeline %s: %s",
            run_id, exc, exc_info=True,
        )
        _mark_failed(db, run_id, str(exc))
        _publish_terminal_event(run_id, "pipeline_failed", str(exc))
        return {"run_id": run_id, "status": "FAILED"}
    finally:
        db.close()


def _should_execute(pipeline_run: PipelineRun) -> bool:
    """Check if the pipeline run should proceed."""
    if pipeline_run.status != PipelineStatus.PENDING:
        logger.warning(
            "Pipeline run %s has status %s, skipping execution",
            pipeline_run.id, pipeline_run.status.value,
        )
        return False
    return True


def _mark_running(db, pipeline_run: PipelineRun) -> None:
    """Mark a pipeline run as RUNNING in the database."""
    pipeline_run.status = PipelineStatus.RUNNING
    pipeline_run.started_at = utcnow()
    db.commit()


def _mark_failed(db, run_id: str, error_message: str) -> None:
    """Mark a pipeline run as FAILED in the database."""
    try:
        pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if pipeline_run:
            pipeline_run.status = PipelineStatus.FAILED
            pipeline_run.completed_at = utcnow()
            pipeline_run.error_message = error_message
            db.commit()
    except Exception as exc:
        logger.error("Failed to mark pipeline %s as FAILED: %s", run_id, exc)
        db.rollback()


def _run_pipeline(db, pipeline_run: PipelineRun):
    """Parse config, load file paths, and execute the pipeline."""
    parser = PipelineParser()
    config = parser.parse(pipeline_run.yaml_config)

    # Load file paths and metadata for load steps
    uploaded_files = db.query(UploadedFile).all()
    file_paths = {f.id: f.stored_path for f in uploaded_files}
    file_metadata = {
        f.id: {"original_filename": f.original_filename}
        for f in uploaded_files
    }

    runner = PipelineRunner()
    progress_callback = make_redis_progress_callback(pipeline_run.id)

    return runner.execute(
        config=config,
        file_paths=file_paths,
        file_metadata=file_metadata,
        run_id=pipeline_run.id,
        progress_callback=progress_callback,
    )


def _persist_results(db, pipeline_run: PipelineRun, summary) -> None:
    """Persist pipeline execution results to the database."""
    if summary.status == RunnerPipelineStatus.COMPLETED:
        pipeline_run.status = PipelineStatus.COMPLETED
        _publish_terminal_event(pipeline_run.id, "pipeline_completed")
    else:
        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.error_message = str(summary.error) if summary.error else None
        _publish_terminal_event(
            pipeline_run.id, "pipeline_failed",
            str(summary.error) if summary.error else "",
        )

    pipeline_run.completed_at = utcnow()
    pipeline_run.total_rows_in = summary.total_rows_processed
    pipeline_run.total_rows_out = (
        summary.step_results[-1].rows_out if summary.step_results else 0
    )

    # Persist step results
    for idx, result in enumerate(summary.step_results):
        step_record = StepResult(
            pipeline_run_id=pipeline_run.id,
            step_name=result.step_name,
            step_type=result.step_type,
            step_index=idx,
            status=StepStatus.COMPLETED,
            rows_in=result.rows_in,
            rows_out=result.rows_out,
            columns_in=result.columns_in,
            columns_out=result.columns_out,
            duration_ms=result.duration_ms,
            warnings=result.warnings,
        )
        db.add(step_record)

    # Persist lineage graph
    lineage_data = summary.lineage.serialize()
    lineage_record = LineageGraph(
        pipeline_run_id=pipeline_run.id,
        graph_data=lineage_data["graph_data"],
        react_flow_data=lineage_data["react_flow_data"],
    )
    db.add(lineage_record)

    db.commit()
    logger.info(
        "Pipeline run %s results persisted: status=%s, duration=%dms",
        pipeline_run.id, pipeline_run.status.value, summary.total_duration_ms,
    )
