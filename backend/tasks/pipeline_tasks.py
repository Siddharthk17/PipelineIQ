"""Celery tasks for asynchronous pipeline execution.

This is a thin orchestration layer. The task does NOT contain business
logic — it loads data from the DB, creates a progress callback that
publishes to Redis, calls PipelineRunner.execute(), and persists
results back to the DB.

Task idempotency: if the same run_id is submitted twice, the task
checks the current status and only proceeds if PENDING.
"""

import logging
from typing import Dict

import orjson
from redis.exceptions import RedisError

from backend.celery_app import celery_app
from backend.config import settings
from backend.database import SessionLocal
from backend.db.redis_pools import get_cache_redis, get_pubsub_redis
from backend.models import (
    HealingAttempt,
    HealingAttemptStatus,
    LineageGraph,
    PipelineRun,
    PipelineStatus,
    PipelineVersion,
    StepResult,
    StepStatus,
    UploadedFile,
)
from backend.pipeline.cache import get_parsed_pipeline
from backend.execution.healing_agent import attempt_heal
from backend.execution.healing_classifier import get_healing_scenario, is_healable
from backend.pipeline.runner import (
    PipelineRunner,
    ProgressCallback,
    StepProgressEvent,
)
from backend.pipeline.runner import PipelineStatus as RunnerPipelineStatus
from backend.pipeline.versioning import save_version
from backend.services.audit_service import log_action
from backend.utils.time_utils import utcnow

logger = logging.getLogger(__name__)


def _status_cache_key(run_id: str) -> str:
    return f"pipeline_progress:last:{run_id}"


def _cache_progress_payload(run_id: str, payload: dict) -> None:
    """Cache latest progress payload for reconnecting SSE clients."""
    try:
        cache_client = get_cache_redis()
        cache_client.setex(_status_cache_key(run_id), 3600, orjson.dumps(payload))
    except RedisError:
        logger.warning("Failed to cache SSE progress payload for run_id=%s", run_id)


def _publish_progress_payload(run_id: str, payload: dict) -> None:
    """Publish a non-terminal progress payload to Redis and cache it."""
    try:
        redis_client = get_pubsub_redis()
        channel = f"pipeline_progress:{run_id}"
        redis_client.publish(channel, orjson.dumps(payload))
    except RedisError:
        logger.warning("Failed to publish progress payload for run_id=%s", run_id)
    _cache_progress_payload(run_id, payload)


def make_redis_progress_callback(run_id: str) -> ProgressCallback:
    """Create a closure that publishes StepProgressEvent to a Redis channel.

    Channel name: f"pipeline_progress:{run_id}"
    Message format: JSON-serialized StepProgressEvent fields.
    """
    def callback(event: StepProgressEvent) -> None:
        """Publish a progress event to Redis."""
        payload = {
            "run_id": event.run_id,
            "step_name": event.step_name,
            "step_index": event.step_index,
            "total_steps": event.total_steps,
            "status": event.status.value,
            "rows_in": event.rows_in,
            "rows_out": event.rows_out,
            "duration_ms": event.duration_ms,
            "error_message": event.error_message,
        }
        _publish_progress_payload(run_id, payload)

    return callback


def _publish_terminal_event(
    run_id: str,
    event_type: str,
    status_value: str,
    error_message: str = "",
) -> None:
    """Publish a terminal pipeline event (completed/failed) to Redis."""
    try:
        redis_client = get_pubsub_redis()
        channel = f"pipeline_progress:{run_id}"
        payload = {
            "run_id": run_id,
            "event_type": event_type,
            "error_message": error_message,
            "status": status_value,
        }
        redis_client.publish(channel, orjson.dumps(payload))
        _cache_progress_payload(run_id, payload)
    except RedisError:
        logger.warning("Failed to publish terminal event for run_id=%s", run_id)


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
    """
    db = SessionLocal()
    try:
        pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()

        if pipeline_run is None:
            logger.error("Pipeline run %s not found in database", run_id)
            return {"run_id": run_id, "status": "NOT_FOUND"}

        if not _should_execute(pipeline_run):
            return {"run_id": run_id, "status": pipeline_run.status.value}

        pipeline_run.celery_task_id = self.request.id
        db.commit()

        _mark_running(db, pipeline_run)
        summary = _execute_with_autonomous_healing(db, pipeline_run)
        _persist_results(db, pipeline_run, summary)

        return {"run_id": run_id, "status": pipeline_run.status.value}

    except Exception as exc:
        logger.error(
            "Unexpected error executing pipeline %s: %s",
            run_id,
            exc,
            exc_info=True,
        )
        _mark_failed(db, run_id, str(exc))
        _publish_terminal_event(
            run_id,
            "pipeline_failed",
            PipelineStatus.FAILED.value,
            str(exc),
        )
        return {"run_id": run_id, "status": "FAILED"}
    finally:
        try:
            from backend.execution.arrow_bus import get_arrow_bus

            get_arrow_bus().cleanup_run(run_id)
        except Exception:
            logger.debug("Arrow bus cleanup skipped for run_id=%s", run_id)
        db.close()


def _should_execute(pipeline_run: PipelineRun) -> bool:
    """Check if the pipeline run should proceed."""
    if pipeline_run.status != PipelineStatus.PENDING:
        logger.warning(
            "Pipeline run %s has status %s, skipping execution",
            pipeline_run.id,
            pipeline_run.status.value,
        )
        return False
    return True


def _mark_running(db, pipeline_run: PipelineRun) -> None:
    """Mark a pipeline run as RUNNING in the database."""
    pipeline_run.status = PipelineStatus.RUNNING
    pipeline_run.started_at = utcnow()
    db.commit()
    _cache_progress_payload(
        str(pipeline_run.id),
        {
            "run_id": str(pipeline_run.id),
            "event_type": "pipeline_started",
            "status": PipelineStatus.RUNNING.value,
        },
    )


def _mark_failed(db, run_id: str, error_message: str) -> None:
    """Mark a pipeline run as FAILED in the database."""
    try:
        pipeline_run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if pipeline_run:
            pipeline_run.status = PipelineStatus.FAILED
            pipeline_run.completed_at = utcnow()
            pipeline_run.error_message = error_message
            db.commit()
            _cache_progress_payload(
                run_id,
                {
                    "run_id": run_id,
                    "event_type": "pipeline_failed",
                    "status": PipelineStatus.FAILED.value,
                    "error_message": error_message,
                },
            )
    except Exception as exc:
        logger.error("Failed to mark pipeline %s as FAILED: %s", run_id, exc)
        db.rollback()


def _run_pipeline(db, pipeline_run: PipelineRun):
    """Parse config, load file paths, and execute the pipeline."""
    config = get_parsed_pipeline(pipeline_run.yaml_config)

    referenced_file_ids = set()
    for step in config.steps:
        file_id = getattr(step, "file_id", None)
        if file_id:
            referenced_file_ids.add(file_id)

    if referenced_file_ids:
        from backend.utils.uuid_utils import as_uuid

        uuid_ids = []
        for fid in referenced_file_ids:
            try:
                uuid_ids.append(as_uuid(fid))
            except (ValueError, AttributeError):
                pass
        uploaded_files = (
            db.query(UploadedFile).filter(UploadedFile.id.in_(uuid_ids)).all()
            if uuid_ids
            else []
        )
    else:
        uploaded_files = []

    file_paths = {str(f.id): f.stored_path for f in uploaded_files}
    file_metadata = {
        str(f.id): {"original_filename": f.original_filename} for f in uploaded_files
    }

    runner = PipelineRunner()
    progress_callback = make_redis_progress_callback(str(pipeline_run.id))

    return runner.execute(
        config=config,
        file_paths=file_paths,
        file_metadata=file_metadata,
        run_id=str(pipeline_run.id),
        progress_callback=progress_callback,
    )


def _execute_with_autonomous_healing(db, pipeline_run: PipelineRun):
    """Execute a pipeline and auto-heal schema drift failures when possible."""
    summary = _run_pipeline(db, pipeline_run)
    if summary.status == RunnerPipelineStatus.COMPLETED:
        return summary

    if (
        not settings.AUTONOMOUS_HEALING_ENABLED
        or int(settings.AUTONOMOUS_HEALING_MAX_ATTEMPTS) <= 0
        or summary.error is None
    ):
        return summary

    run_id = str(pipeline_run.id)
    failed_step_name = getattr(summary.error, "step_name", "") or ""

    if not is_healable(summary.error):
        attempt = HealingAttempt(
            run_id=pipeline_run.id,
            pipeline_name=pipeline_run.name,
            attempt_number=_next_healing_attempt_number(db, pipeline_run.id),
            status=HealingAttemptStatus.NON_HEALABLE,
            failed_step=failed_step_name or None,
            error_type=summary.error.__class__.__name__,
            error_message=str(summary.error),
            classification_reason=get_healing_scenario(summary.error)
            or "Error is not healable automatically",
            applied=False,
            completed_at=utcnow(),
        )
        db.add(attempt)
        db.commit()
        _publish_progress_payload(
            run_id,
            {
                "run_id": run_id,
                "event_type": "healing_non_healable",
                "status": pipeline_run.status.value,
                "attempt_number": attempt.attempt_number,
                "failed_step": failed_step_name,
                "reason": attempt.classification_reason,
            },
        )
        return summary

    next_attempt_number = _next_healing_attempt_number(db, pipeline_run.id)
    pipeline_run.status = PipelineStatus.HEALING
    db.commit()
    _publish_progress_payload(
        run_id,
        {
            "run_id": run_id,
            "event_type": "healing_started",
            "status": PipelineStatus.HEALING.value,
            "failed_step": failed_step_name,
            "error_type": summary.error.__class__.__name__,
            "error_message": str(summary.error),
        },
    )
    _publish_progress_payload(
        run_id,
        {
            "run_id": run_id,
            "event_type": "healing_attempt_started",
            "status": PipelineStatus.HEALING.value,
            "attempt_number": next_attempt_number,
            "failed_step": failed_step_name,
        },
    )

    healing_result = attempt_heal(
        run_id=run_id,
        pipeline_name=pipeline_run.name,
        failed_step=failed_step_name,
        error=summary.error,
        pipeline_yaml=pipeline_run.yaml_config,
        file_ids=_collect_file_ids_from_config(pipeline_run.yaml_config),
        db=db,
    )
    if not healing_result.success or not healing_result.patched_yaml:
        _publish_progress_payload(
            run_id,
            {
                "run_id": run_id,
                "event_type": "healing_failed",
                "status": PipelineStatus.FAILED.value,
                "attempts": healing_result.attempts,
                "failed_step": failed_step_name,
                "error_message": healing_result.error,
            },
        )
        return summary

    pipeline_run.yaml_config = healing_result.patched_yaml
    pipeline_run.error_message = None
    db.commit()
    _publish_progress_payload(
        run_id,
        {
            "run_id": run_id,
            "event_type": "healing_attempt_applied",
            "status": PipelineStatus.HEALING.value,
            "attempt_number": healing_result.attempts,
            "failed_step": failed_step_name,
        },
    )
    _save_version_if_needed(db=db, pipeline_run=pipeline_run)
    _record_healing_audit(
        db=db,
        pipeline_run=pipeline_run,
        healing_result=healing_result,
        failed_step=failed_step_name,
    )

    retry_summary = _run_pipeline(db, pipeline_run)
    if retry_summary.status == RunnerPipelineStatus.COMPLETED:
        pipeline_run.status = PipelineStatus.HEALED
        db.commit()
        _publish_progress_payload(
            run_id,
            {
                "run_id": run_id,
                "event_type": "healing_complete",
                "status": PipelineStatus.HEALED.value,
                "attempts": healing_result.attempts,
                "failed_step": failed_step_name,
                "confidence": healing_result.confidence,
                "description": healing_result.description,
                "patch": healing_result.patch,
            },
        )
        _publish_progress_payload(
            run_id,
            {
                "run_id": run_id,
                "event_type": "healing_succeeded",
                "status": PipelineStatus.HEALED.value,
                "attempt_number": healing_result.attempts,
            },
        )
        return retry_summary

    _publish_progress_payload(
        run_id,
        {
            "run_id": run_id,
            "event_type": "healing_failed",
            "status": PipelineStatus.FAILED.value,
            "attempts": healing_result.attempts,
            "failed_step": getattr(retry_summary.error, "step_name", failed_step_name),
            "error_message": str(retry_summary.error) if retry_summary.error else None,
        },
    )
    _publish_progress_payload(
        run_id,
        {
            "run_id": run_id,
            "event_type": "healing_retry_failed",
            "status": PipelineStatus.FAILED.value,
            "attempt_number": healing_result.attempts,
            "failed_step": getattr(retry_summary.error, "step_name", failed_step_name),
            "error_message": str(retry_summary.error) if retry_summary.error else None,
        },
    )
    return retry_summary


def _next_healing_attempt_number(db, pipeline_run_id) -> int:
    return (
        db.query(HealingAttempt)
        .filter(HealingAttempt.run_id == pipeline_run_id)
        .count()
        + 1
    )


def _collect_file_ids_from_config(yaml_config: str) -> list[str]:
    parsed_pipeline = get_parsed_pipeline(yaml_config)
    file_ids: list[str] = []
    seen_file_ids: set[str] = set()
    for step in parsed_pipeline.steps:
        file_id = getattr(step, "file_id", None)
        if isinstance(file_id, str) and file_id and file_id not in seen_file_ids:
            seen_file_ids.add(file_id)
            file_ids.append(file_id)
    return file_ids


def _save_version_if_needed(*, db, pipeline_run: PipelineRun) -> None:
    config = get_parsed_pipeline(pipeline_run.yaml_config)
    latest_version = (
        db.query(PipelineVersion)
        .filter(PipelineVersion.pipeline_name == config.name)
        .order_by(PipelineVersion.version_number.desc())
        .first()
    )
    if latest_version and latest_version.yaml_config == pipeline_run.yaml_config:
        return

    save_version(
        pipeline_name=config.name,
        yaml_config=pipeline_run.yaml_config,
        run_id=pipeline_run.id,
        db=db,
    )


def _record_healing_audit(*, db, pipeline_run: PipelineRun, healing_result, failed_step: str) -> None:
    log_action(
        db,
        "pipeline_auto_healed",
        user_id=pipeline_run.user_id,
        resource_type="pipeline",
        resource_id=pipeline_run.id,
        details={
            "pipeline_name": pipeline_run.name,
            "failed_step": failed_step,
            "attempts": healing_result.attempts,
            "confidence": healing_result.confidence,
            "description": healing_result.description,
            "schema_diff": healing_result.schema_diff,
            "patch": healing_result.patch,
        },
    )


def _persist_results(db, pipeline_run: PipelineRun, summary) -> None:
    """Persist pipeline execution results to the database."""
    from backend.metrics import PIPELINE_RUNS_TOTAL, PIPELINE_DURATION_SECONDS

    healed_run = (
        pipeline_run.status == PipelineStatus.HEALED
        or db.query(HealingAttempt)
        .filter(
            HealingAttempt.run_id == pipeline_run.id,
            HealingAttempt.applied.is_(True),
        )
        .count()
        > 0
    )

    if summary.status == RunnerPipelineStatus.COMPLETED:
        pipeline_run.status = (
            PipelineStatus.HEALED if healed_run else PipelineStatus.COMPLETED
        )
        PIPELINE_RUNS_TOTAL.labels(
            status="healed" if pipeline_run.status == PipelineStatus.HEALED else "success"
        ).inc()
        event_type = "pipeline_completed"
        _publish_terminal_event(
            str(pipeline_run.id),
            event_type,
            pipeline_run.status.value,
        )
    else:
        pipeline_run.status = PipelineStatus.FAILED
        pipeline_run.error_message = str(summary.error) if summary.error else None
        PIPELINE_RUNS_TOTAL.labels(status="failed").inc()
        event_type = "pipeline_failed"
        _publish_terminal_event(
            str(pipeline_run.id),
            event_type,
            pipeline_run.status.value,
            str(summary.error) if summary.error else "",
        )

    pipeline_run.completed_at = utcnow()
    duration = 0.0
    if pipeline_run.started_at and pipeline_run.completed_at:
        duration = (pipeline_run.completed_at - pipeline_run.started_at).total_seconds()
        PIPELINE_DURATION_SECONDS.observe(duration)
    pipeline_run.total_rows_in = summary.total_rows_processed
    pipeline_run.total_rows_out = (
        summary.step_results[-1].rows_out if summary.step_results else 0
    )

    try:
        from backend.tasks.notification_tasks import deliver_notifications_task

        deliver_notifications_task.delay(
            run_id=str(pipeline_run.id),
            event_type=event_type,
            pipeline_name=pipeline_run.name or "",
            status=pipeline_run.status.value,
            error_message=pipeline_run.error_message or "",
            user_id=str(pipeline_run.user_id) if pipeline_run.user_id else "",
        )
    except Exception as exc:
        logger.error(
            "Failed to queue notification task for run %s: %s", pipeline_run.id, exc
        )

    try:
        from backend.tasks.webhook_tasks import deliver_webhooks_task

        status_str = (
            "completed"
            if summary.status == RunnerPipelineStatus.COMPLETED
            else "failed"
        )
        duration_ms = int(duration * 1000)
        deliver_webhooks_task.delay(
            run_id=str(pipeline_run.id),
            status=status_str,
            pipeline_name=pipeline_run.name or "",
            duration_ms=duration_ms,
            steps_count=len(summary.step_results),
            rows_processed=summary.total_rows_processed or 0,
            user_id=str(pipeline_run.user_id) if pipeline_run.user_id else "",
        )
    except Exception as exc:
        logger.error(
            "Failed to queue webhook task for run %s: %s", pipeline_run.id, exc
        )

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

    lineage_data = summary.lineage.serialize()
    lineage_record = LineageGraph(
        pipeline_run_id=pipeline_run.id,
        graph_data=lineage_data["graph_data"],
        react_flow_data=lineage_data["react_flow_data"],
    )
    db.add(lineage_record)

    db.commit()

    try:
        _save_version_if_needed(db=db, pipeline_run=pipeline_run)
    except Exception as exc:
        # Keep session usable even if version write fails after persistence commit.
        db.rollback()
        logger.warning("Failed to save pipeline version: %s", exc)

    logger.info(
        "Pipeline run %s results persisted: status=%s, duration=%dms",
        pipeline_run.id,
        pipeline_run.status.value,
        summary.total_duration_ms,
    )
