"""Pipeline execution API endpoints.

Provides endpoints for pipeline validation, execution (async via Celery),
result retrieval, cancellation, and export.
"""

import logging
import orjson
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PipelineRun, PipelineStatus, UploadedFile, User
from datetime import timezone
from backend.pipeline.parser import PipelineParser
from backend.pipeline.planner import generate_execution_plan
from backend.schemas import (
    PipelineRunListResponse,
    PipelineRunResponse,
    RunPipelineRequest,
    RunPipelineResponse,
    StepResultResponse,
    ValidatePipelineRequest,
    ValidatePipelineResponse,
    ValidationErrorDetail,
    ValidationWarningDetail,
)
from backend.tasks.pipeline_tasks import execute_pipeline_task
from backend.db.redis_pools import get_cache_redis, get_pubsub_redis
from backend.utils.rate_limiter import limiter
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import (
    validate_uuid_format as _validate_uuid_format,
    as_uuid as _as_uuid,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
legacy_runs_router = APIRouter(prefix="/runs", tags=["runs"])


def _create_and_queue_pipeline_run(
    *,
    yaml_config: str,
    name: str | None,
    request: Request,
    db: Session,
    current_user: User,
) -> RunPipelineResponse:
    """Create a PipelineRun record and queue Celery execution."""
    parser = PipelineParser()
    config = parser.parse(yaml_config)
    pipeline_name = name or config.name

    pipeline_run = PipelineRun(
        name=pipeline_name,
        status=PipelineStatus.PENDING,
        yaml_config=yaml_config,
        user_id=current_user.id if current_user else None,
    )
    db.add(pipeline_run)
    db.commit()
    db.refresh(pipeline_run)

    result = execute_pipeline_task.delay(str(pipeline_run.id))
    pipeline_run.celery_task_id = result.id
    db.commit()

    log_action(
        db,
        "pipeline_run",
        user_id=current_user.id if current_user else None,
        resource_type="pipeline",
        resource_id=pipeline_run.id,
        details={"name": pipeline_name},
        request=request,
    )

    logger.info("Pipeline run queued: id=%s, name=%s", pipeline_run.id, pipeline_name)
    return RunPipelineResponse(
        run_id=str(pipeline_run.id), status=pipeline_run.status.value
    )


@router.post(
    "/validate",
    response_model=ValidatePipelineResponse,
    summary="Validate pipeline configuration",
    description="Validate a YAML pipeline configuration without executing it.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def validate_pipeline(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> ValidatePipelineResponse:
    """Validate a pipeline configuration against registered files."""
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)

    registered_ids = {str(row[0]) for row in db.query(UploadedFile.id).all()}
    result = parser.validate(config, registered_ids)

    return ValidatePipelineResponse(
        is_valid=result.is_valid,
        errors=[
            ValidationErrorDetail(
                step_name=e.step_name,
                field=e.field,
                message=e.message,
                suggestion=e.suggestion,
            )
            for e in result.errors
        ],
        warnings=[
            ValidationWarningDetail(
                step_name=w.step_name,
                message=w.message,
            )
            for w in result.warnings
        ],
    )


@router.post(
    "/plan",
    summary="Generate dry-run execution plan",
    description="Generate an execution plan without actually running the pipeline.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def plan_pipeline(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Generate a dry-run execution plan for a pipeline."""
    plan = generate_execution_plan(body.yaml_config, db)

    return {
        "pipeline_name": plan.pipeline_name,
        "total_steps": plan.total_steps,
        "estimated_total_duration_ms": plan.estimated_total_duration_ms,
        "files_read": plan.files_read,
        "files_written": plan.files_written,
        "estimated_rows_processed": plan.estimated_rows_processed,
        "will_succeed": plan.will_succeed,
        "warnings": plan.warnings,
        "steps": [
            {
                "step_index": s.step_index,
                "step_name": s.step_name,
                "step_type": s.step_type,
                "input_step": s.input_step,
                "input_file_id": s.input_file_id,
                "estimated_rows_in": s.estimated_rows_in,
                "estimated_rows_out": s.estimated_rows_out,
                "estimated_columns": s.estimated_columns,
                "estimated_duration_ms": s.estimated_duration_ms,
                "warnings": s.warnings,
                "will_fail": s.will_fail,
                "fail_reason": s.fail_reason,
            }
            for s in plan.steps
        ],
    }


def _check_pipeline_permission(
    db: Session,
    user: User,
    pipeline_name: str,
    required_levels: list[str],
    grant_owner: bool = False,
) -> None:
    """Verify user has required permission level for the pipeline."""
    if user.role == "admin":
        return

    from backend.models import PipelinePermission

    permission = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == user.id,
        )
        .first()
    )

    if not permission:
        logger.info(
            "No permission found for user %s on pipeline %s. grant_owner=%s",
            user.id,
            pipeline_name,
            grant_owner,
        )
        if grant_owner and user.role != "viewer":
            # First time this pipeline is run/created - grant ownership
            new_perm = PipelinePermission(
                pipeline_name=pipeline_name,
                user_id=user.id,
                permission_level="owner",
            )
            db.add(new_perm)
            db.commit()
            logger.info(
                "Granted owner permission to user %s for pipeline %s",
                user.id,
                pipeline_name,
            )
            return

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks required permissions ({', '.join(required_levels)}) to perform this action on pipeline '{pipeline_name}'",
        )

    if permission.permission_level not in required_levels:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks required permissions ({', '.join(required_levels)}) to perform this action on pipeline '{pipeline_name}'",
        )


@router.post(
    "/run",
    response_model=RunPipelineResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a pipeline run",
    description="Queue a pipeline for asynchronous execution. Returns immediately.",
)
@limiter.limit(settings.RATE_LIMIT_PIPELINE_RUN)
def run_pipeline(
    request: Request,
    body: RunPipelineRequest,
    response: Response,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> RunPipelineResponse:
    """Create a pipeline run record and queue it for execution."""
    # Parse YAML to get the pipeline name for permission check
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)
    pipeline_name = body.name or config.name

    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner"], grant_owner=True
    )

    # Verify all referenced file IDs exist in the database and general config is valid
    registered_ids = {str(row[0]) for row in db.query(UploadedFile.id).all()}
    validation_result = parser.validate(config, registered_ids)

    if not validation_result.is_valid:
        # Specifically check for file registration errors to return 404
        for error in validation_result.errors:
            if (
                "not found" in error.message.lower()
                or "registered" in error.message.lower()
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Referenced file not found: {error.message}",
                )

        # Otherwise, return all validation errors as 400
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Pipeline configuration is invalid",
                "errors": [
                    {
                        "step_name": e.step_name,
                        "field": e.field,
                        "message": e.message,
                        "suggestion": e.suggestion,
                    }
                    for e in validation_result.errors
                ],
            },
        )

    return _create_and_queue_pipeline_run(
        yaml_config=body.yaml_config,
        name=body.name,
        request=request,
        db=db,
        current_user=current_user,
    )


@legacy_runs_router.post(
    "",
    response_model=RunPipelineResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a pipeline run (legacy)",
    description="Legacy compatibility endpoint that accepts either yaml or yaml_config.",
)
@limiter.limit(settings.RATE_LIMIT_PIPELINE_RUN)
async def run_pipeline_legacy(
    request: Request,
    response: Response,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> RunPipelineResponse:
    """Legacy endpoint for prompt compatibility: POST /api/runs."""
    payload: dict = {}
    raw_body = await request.body()
    if raw_body:
        try:
            parsed = orjson.loads(raw_body)
            if isinstance(parsed, dict):
                payload = parsed
        except orjson.JSONDecodeError:
            payload = {}

    yaml_config = payload.get("yaml_config")
    if yaml_config is None:
        yaml_config = payload.get("yaml")
    if not isinstance(yaml_config, str) or not yaml_config.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Request body must include 'yaml_config' (or legacy 'yaml')",
        )
    name = payload.get("name")
    if name is not None and not isinstance(name, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="'name' must be a string when provided",
        )
    return _create_and_queue_pipeline_run(
        yaml_config=yaml_config,
        name=name,
        request=request,
        db=db,
        current_user=current_user,
    )


@router.get(
    "/",
    response_model=PipelineRunListResponse,
    summary="List pipeline runs",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def list_pipeline_runs(
    request: Request,
    response: Response,
    page: int = 1,
    limit: int = 20,
    status_filter: Optional[str] = None,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> PipelineRunListResponse:
    """List pipeline runs with pagination, ordered by creation time."""
    page = max(1, page)
    limit = max(1, min(limit, 100))

    query = db.query(PipelineRun).filter(PipelineRun.user_id == current_user.id)
    if status_filter:
        try:
            ps = PipelineStatus(status_filter.upper())
            query = query.filter(PipelineRun.status == ps)
        except ValueError:
            pass

    total = query.count()
    runs = (
        query.order_by(PipelineRun.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return PipelineRunListResponse(
        runs=[_run_to_response(r) for r in runs],
        total=total,
    )


@router.get(
    "/stats",
    summary="Get pipeline statistics",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_pipeline_stats(
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Get aggregate pipeline statistics."""
    from sqlalchemy import func

    total_runs = (
        db.query(func.count(PipelineRun.id))
        .filter(PipelineRun.user_id == current_user.id)
        .scalar()
        or 0
    )
    completed = (
        db.query(func.count(PipelineRun.id))
        .filter(
            PipelineRun.status == PipelineStatus.COMPLETED,
            PipelineRun.user_id == current_user.id,
        )
        .scalar()
        or 0
    )
    failed = (
        db.query(func.count(PipelineRun.id))
        .filter(
            PipelineRun.status == PipelineStatus.FAILED,
            PipelineRun.user_id == current_user.id,
        )
        .scalar()
        or 0
    )
    pending = (
        db.query(func.count(PipelineRun.id))
        .filter(
            PipelineRun.status == PipelineStatus.PENDING,
            PipelineRun.user_id == current_user.id,
        )
        .scalar()
        or 0
    )
    total_files = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.user_id == current_user.id)
        .scalar()
        or 0
    )
    return {
        "total_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "success_rate": round(completed / total_runs * 100, 1) if total_runs > 0 else 0,
        "total_files": total_files,
    }


@router.get(
    "/{run_id}",
    response_model=PipelineRunResponse,
    summary="Get pipeline run details",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_pipeline_run(
    run_id: str,
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> PipelineRunResponse:
    """Get full details of a specific pipeline run."""
    logger.info("DEBUG: User %s accessing run %s", current_user.id, run_id)
    _validate_uuid_format(run_id)
    pipeline_run = (
        db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    )
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_pipeline_permission(
        db, current_user, pipeline_run.name, ["owner", "runner", "viewer"]
    )

    return _run_to_response(pipeline_run)


def _ensure_utc(dt):
    """Attach UTC tzinfo to naive datetimes from SQLite."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _run_to_response(pipeline_run: PipelineRun) -> PipelineRunResponse:
    """Convert a PipelineRun ORM model to an API response."""
    return PipelineRunResponse(
        id=str(pipeline_run.id),
        name=pipeline_run.name,
        status=pipeline_run.status.value,
        created_at=_ensure_utc(pipeline_run.created_at),
        started_at=_ensure_utc(pipeline_run.started_at),
        completed_at=_ensure_utc(pipeline_run.completed_at),
        duration_ms=pipeline_run.duration_ms,
        total_rows_in=pipeline_run.total_rows_in,
        total_rows_out=pipeline_run.total_rows_out,
        error_message=pipeline_run.error_message,
        step_results=[
            StepResultResponse(
                step_name=sr.step_name,
                step_type=sr.step_type,
                step_index=sr.step_index,
                status=sr.status.value,
                rows_in=sr.rows_in,
                rows_out=sr.rows_out,
                columns_in=sr.columns_in,
                columns_out=sr.columns_out,
                duration_ms=sr.duration_ms,
                warnings=sr.warnings,
                error_message=sr.error_message,
            )
            for sr in pipeline_run.step_results
        ],
    )


def _status_cache_key(run_id: str) -> str:
    return f"pipeline_progress:last:{run_id}"


@router.post(
    "/{run_id}/cancel",
    summary="Cancel a running pipeline",
    description="Cancel a PENDING or RUNNING pipeline run and revoke its Celery task.",
)
def cancel_pipeline_run(
    run_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel a pipeline run by setting status to CANCELLED and revoking the task."""
    _validate_uuid_format(run_id)
    pipeline_run = (
        db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    )
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_pipeline_permission(db, current_user, pipeline_run.name, ["owner", "runner"])

    if pipeline_run.status not in (PipelineStatus.PENDING, PipelineStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel pipeline with status '{pipeline_run.status.value}'",
        )

    # Revoke the Celery task
    from backend.celery_app import celery_app as _celery_app

    task_id = pipeline_run.celery_task_id or run_id
    _celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    from backend.utils.time_utils import utcnow

    pipeline_run.status = PipelineStatus.CANCELLED
    pipeline_run.completed_at = utcnow()
    pipeline_run.error_message = "Cancelled by user"
    db.commit()

    cancel_payload = {
        "run_id": run_id,
        "event_type": "pipeline_cancelled",
        "status": PipelineStatus.CANCELLED.value,
        "error_message": "Cancelled by user",
    }
    try:
        get_pubsub_redis().publish(
            f"pipeline_progress:{run_id}",
            orjson.dumps(cancel_payload),
        )
        get_cache_redis().setex(
            _status_cache_key(run_id), 3600, orjson.dumps(cancel_payload)
        )
    except RedisError:
        logger.warning("Failed to publish cancellation SSE event for run_id=%s", run_id)

    log_action(
        db,
        "pipeline_cancelled",
        user_id=current_user.id,
        resource_type="pipeline",
        resource_id=pipeline_run.id,
        request=request,
    )

    logger.info("Pipeline run cancelled: id=%s", run_id)
    return {"run_id": run_id, "status": "CANCELLED"}


@router.post(
    "/preview",
    summary="Preview sample data at a pipeline step",
    description="Parse and dry-run pipeline up to a step, returning first 5 rows.",
)
@limiter.limit(settings.RATE_LIMIT_VALIDATION)
def preview_pipeline_step(
    request: Request,
    body: ValidatePipelineRequest,
    response: Response,
    step_index: int = 0,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Preview sample data at a specific step of a pipeline."""
    parser = PipelineParser()
    config = parser.parse(body.yaml_config)

    if step_index < 0 or step_index >= len(config.steps):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"step_index {step_index} out of range (0..{len(config.steps) - 1})",
        )

    plan = generate_execution_plan(body.yaml_config, db)

    step_info = None
    if step_index < len(plan.steps):
        s = plan.steps[step_index]
        step_info = {
            "step_name": s.step_name,
            "step_type": s.step_type,
            "estimated_rows_in": s.estimated_rows_in,
            "estimated_rows_out": s.estimated_rows_out,
            "estimated_columns": s.estimated_columns,
        }

    return {
        "pipeline_name": config.name,
        "step_index": step_index,
        "total_steps": len(config.steps),
        "step_preview": step_info,
        "note": "Full sample data preview requires pipeline execution. Use /plan for detailed estimates.",
    }


@router.get(
    "/{run_id}/export",
    summary="Export pipeline run output",
    description="Download the output file from a completed pipeline run.",
)
def export_pipeline_output(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Export/download the output file from a completed pipeline run."""
    from pathlib import Path
    from fastapi.responses import FileResponse

    _validate_uuid_format(run_id)
    pipeline_run = (
        db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    )
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_pipeline_permission(db, current_user, pipeline_run.name, ["owner", "runner"])

    if pipeline_run.status != PipelineStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pipeline run is not completed (status: {pipeline_run.status.value})",
        )

    # Look for output files in the uploads directory matching known save conventions.
    output_dir = settings.UPLOAD_DIR
    import glob as glob_module
    import yaml

    output_files: list[str] = []
    seen: set[str] = set()

    def _add_matches(pattern: Path) -> None:
        for match in glob_module.glob(str(pattern)):
            if match not in seen:
                seen.add(match)
                output_files.append(match)

    # Older conventions may include run_id in output filenames.
    for pattern in [output_dir / f"{run_id}*.csv", output_dir / f"{run_id}*.json"]:
        _add_matches(pattern)

    # Current save step writes: "{filename}_{uuid}.csv"
    for sr in pipeline_run.step_results:
        if sr.step_type != "save":
            continue
        for ext in [".csv", ".json"]:
            _add_matches(output_dir / f"{sr.step_name}_*{ext}")
            _add_matches(output_dir / f"{sr.step_name}{ext}")

    # Parse YAML and look for save.filename values.
    try:
        parsed = yaml.safe_load(pipeline_run.yaml_config) or {}
        steps = parsed.get("pipeline", {}).get("steps", [])
        for step in steps:
            if step.get("type") != "save":
                continue
            filename = str(step.get("filename", "")).strip()
            if not filename:
                continue
            for ext in [".csv", ".json"]:
                _add_matches(output_dir / filename)
                _add_matches(output_dir / f"{filename}_*{ext}")
                if not filename.endswith(ext):
                    _add_matches(output_dir / f"{filename}{ext}")
    except (TypeError, ValueError, yaml.YAMLError):
        logger.warning(
            "Could not parse YAML while locating export file for run_id=%s", run_id
        )

    if not output_files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No output file found for this pipeline run",
        )

    output_path = Path(output_files[0])
    media_type = "text/csv" if output_path.suffix == ".csv" else "application/json"

    return FileResponse(
        path=str(output_path),
        media_type=media_type,
        filename=output_path.name,
    )
