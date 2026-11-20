"""Pipeline execution API endpoints.

Provides endpoints for pipeline validation, execution (async via Celery),
result retrieval, cancellation, and export.
"""

import logging
import os
import time
from datetime import UTC

import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from backend.api.authz import canonical_pipeline_name
from backend.auth import get_current_user
from backend.config import settings
from backend.db.redis_pools import get_cache_redis, get_pubsub_redis
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import (
    HealingAttempt,
    PipelineRun,
    PipelineStatus,
    SchemaSnapshot,
    StepResult,
    UploadedFile,
    User,
)
from backend.models.data_contract import IdempotencyKey
from backend.pipeline.cache import get_parsed_pipeline
from backend.pipeline.parser import PipelineParser, StepType
from backend.pipeline.planner import generate_execution_plan
from backend.schemas import (
    HealingAttemptResponse,
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
from backend.services.audit_service import log_action
from backend.services.pipeline_validation import (
    collect_schema_validation_errors,
    serialize_validation_errors,
)
from backend.tasks.pipeline_tasks import execute_pipeline_task
from backend.tasks.streaming_pipeline import run_streaming_pipeline
from backend.utils.rate_limiter import limiter
from backend.utils.sse_security import sign_sse_payload
from backend.utils.uuid_utils import (
    as_uuid as _as_uuid,
)
from backend.utils.uuid_utils import (
    validate_uuid_format as _validate_uuid_format,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
legacy_runs_router = APIRouter(prefix="/runs", tags=["runs"])
_pipeline_parser = PipelineParser()


def _create_and_queue_pipeline_run(
    *,
    yaml_config: str,
    name: str | None,
    parsed_config=None,
    request: Request,
    db: Session,
    current_user: User,
) -> RunPipelineResponse:
    """Create a PipelineRun record and queue Celery execution."""
    config = parsed_config or get_parsed_pipeline(yaml_config)
    # Canonical identity (M-064): the explicit request name wins, else the
    # YAML name. This is the same resolver the permission check validated,
    # so the grant and the stored run can never diverge.
    pipeline_name = canonical_pipeline_name(name, config.name)

    pipeline_run = PipelineRun(
        name=pipeline_name,
        status=PipelineStatus.PENDING,
        yaml_config=yaml_config,
        user_id=current_user.id if current_user else None,
    )
    db.add(pipeline_run)
    db.commit()
    db.refresh(pipeline_run)
    _create_run_schema_snapshots(
        db=db,
        run_id=pipeline_run.id,
        parsed_config=config)

    has_stream_consume = any(
        getattr(s, "step_type", None) in ("stream_consume", StepType.STREAM_CONSUME)
        or (hasattr(s, "type") and getattr(s, "type", "") in ("stream_consume", "stream_consume"))
        for s in config.steps
    )

    if has_stream_consume:
        result = run_streaming_pipeline.delay(str(pipeline_run.id))
        pipeline_run.celery_task_id = result.id
        pipeline_run.status = PipelineStatus.STREAMING_ACTIVE
    else:
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

    logger.info(
        "Pipeline run queued: id=%s, name=%s",
        pipeline_run.id,
        pipeline_name)
    return RunPipelineResponse(
        run_id=str(pipeline_run.id), status=pipeline_run.status.value
    )


def _create_run_schema_snapshots(
    *,
    db: Session,
    run_id,
        parsed_config) -> None:
    """Capture the source file schema at run submission time for healing diffing."""
    referenced_file_ids = []
    seen_file_ids: set[str] = set()
    for step in getattr(parsed_config, "steps", []):
        file_id = getattr(step, "file_id", None)
        if isinstance(
                file_id,
                str) and file_id and file_id not in seen_file_ids:
            seen_file_ids.add(file_id)
            referenced_file_ids.append(_as_uuid(file_id))

    if not referenced_file_ids:
        return

    uploaded_files = (
        db.query(UploadedFile)
        .filter(UploadedFile.id.in_(referenced_file_ids))
        .all()
    )
    for uploaded_file in uploaded_files:
        db.add(
            SchemaSnapshot(
                file_id=uploaded_file.id,
                run_id=run_id,
                columns=uploaded_file.columns,
                dtypes=uploaded_file.dtypes,
                row_count=uploaded_file.row_count,
            )
        )
    db.commit()


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
    config = get_parsed_pipeline(body.yaml_config)

    registered_ids = _owned_file_ids(db, current_user)
    result = _pipeline_parser.validate(config, registered_ids)
    if result.is_valid:
        result.errors.extend(collect_schema_validation_errors(config, db))
        result.is_valid = len(result.errors) == 0

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
    config = get_parsed_pipeline(body.yaml_config)
    validation_result = _pipeline_parser.validate(
        config, _owned_file_ids(db, current_user))
    if not validation_result.is_valid:
        capped = validation_result.errors[:25]
        truncated = len(validation_result.errors) - len(capped)
        error_list = serialize_validation_errors(capped)
        if truncated > 0:
            error_list.append({
                "step_name": "",
                "field": "",
                "message": f"... and {truncated} more errors (truncated)",
                "suggestion": "Fix the errors above first, then re-validate.",
            })
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Pipeline configuration is invalid",
                "errors": error_list,
            },
        )
    plan = generate_execution_plan(
        body.yaml_config, db,
        user_id=current_user.id, is_admin=current_user.role == "admin",
    )

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


def _owned_file_ids(db: Session, user: User) -> set[str]:
    """IDs of files the user may reference in a pipeline.

    Admins may use any file; everyone else is limited to their own uploads.
    Referencing another user's file behaves exactly like referencing a
    missing file (404), so no ownership information leaks.
    """
    query = db.query(UploadedFile.id)
    if user.role != "admin":
        query = query.filter(UploadedFile.user_id == user.id)
    return {str(row[0]) for row in query.all()}


def _validate_claim_name(pipeline_name: str | None) -> str:
    """Validate the effective pipeline/run name before any ownership claim.

    Names are validated BEFORE the first-run OWNER grant (M-089) so a
    caller cannot squat or probe names with requests that will fail
    validation anyway. Delegates to the shared canonical resolver
    (backend.api.authz) so the claim, the stored run, and the saved
    version all use one identity (M-064).
    """
    try:
        return canonical_pipeline_name(pipeline_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _get_idempotency_key(request: Request) -> str | None:
    """Extract and validate the Idempotency-Key header (M-062)."""
    raw = request.headers.get("idempotency-key")
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > 255 or any(c in key for c in ("\r", "\n")):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid Idempotency-Key header: must be 1-255 characters "
            "without line breaks",
        )
    return key


_IDEMPOTENT_INFLIGHT_TIMEOUT_S = 5.0
_IDEMPOTENT_POLL_INTERVAL_S = 0.2


def _idempotency_claim(
    db: Session, *, scope: str, key: str, user_id
) -> tuple[str, IdempotencyKey | None]:
    """Claim an idempotency key or return the stored outcome (M-062).

    Returns ("owner", row) when this request won the key, ("replay", row)
    when a completed response is stored, or ("inflight", row) when another
    request is still producing the response.
    """
    existing = (
        db.query(IdempotencyKey)
        .filter(
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == key,
            IdempotencyKey.user_id == user_id,
        )
        .first()
    )
    if existing is not None:
        if existing.status_code is not None:
            return "replay", existing
        return "inflight", existing
    row = IdempotencyKey(
        scope=scope, key=key, user_id=user_id,
        status_code=None, response_body=None,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.scope == scope,
                IdempotencyKey.key == key,
                IdempotencyKey.user_id == user_id,
            )
            .first()
        )
        if existing is not None and existing.status_code is not None:
            return "replay", existing
        return "inflight", existing
    db.refresh(row)
    return "owner", row


def _idempotency_wait(db: Session, row: IdempotencyKey) -> IdempotencyKey | None:
    """Wait briefly for an in-flight duplicate to complete (M-062)."""
    deadline = time.monotonic() + _IDEMPOTENT_INFLIGHT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(_IDEMPOTENT_POLL_INTERVAL_S)
        refreshed = (
            db.query(IdempotencyKey).filter(IdempotencyKey.id == row.id).first()
        )
        if refreshed is not None and refreshed.status_code is not None:
            return refreshed
    return None


def _idempotency_release(db: Session, row: IdempotencyKey) -> None:
    """Release a claim placeholder that never produced a response (M-062).

    Only removes the row while it is still incomplete, so a concurrent
    completion can never be deleted by a loser.
    """
    try:
        doomed = (
            db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.id == row.id,
                IdempotencyKey.status_code.is_(None),
            )
            .first()
        )
        if doomed is not None:
            db.delete(doomed)
            db.commit()
    except Exception:  # best-effort cleanup only
        logger.warning("Failed to release idempotency placeholder", exc_info=True)
        db.rollback()


def _idempotency_complete(
    db: Session,
    row: IdempotencyKey,
    *,
    status_code: int,
    body: dict,
    resource_id: str | None = None,
) -> None:
    row.status_code = status_code
    row.response_body = body
    row.resource_id = resource_id
    db.add(row)
    db.commit()


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

    from backend.models import PipelineContract, PipelinePermission

    # Owning the contract for a pipeline name is ownership of the pipeline
    # itself (mirrors _ensure_pipeline_contract_access in contracts.py).
    own_contract = (
        db.query(PipelineContract.id)
        .filter(
            PipelineContract.pipeline_name == pipeline_name,
            PipelineContract.user_id == user.id,
        )
        .first()
    )
    if own_contract:
        return

    permission = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == user.id,
        )
        .first()
    )

    if not permission:
        logger.debug(
            "No permission found for user %s on pipeline %s. grant_owner=%s",
            user.id,
            pipeline_name,
            grant_owner,
        )
        if grant_owner:
            # Self-serve creation: the first user of a brand-new pipeline name
            # becomes its owner. Names already in use by anyone else stay
            # off-limits — otherwise anyone could co-opt another user's
            # pipeline, contracts and versions just by reusing its name.
            # The claim is atomic (M-007): the partial unique index on
            # (pipeline_name) WHERE permission_level='owner' plus the
            # (pipeline_name, user_id) unique constraint turn a concurrent
            # double-claim into an IntegrityError for exactly one loser,
            # which is re-checked below instead of becoming a second OWNER.
            from backend.models import PermissionLevel, PipelineContract, PipelinePermission

            name_in_use = (
                db.query(PipelineRun.id)
                .filter(PipelineRun.name == pipeline_name)
                .first()
                or db.query(PipelineContract.id)
                .filter(PipelineContract.pipeline_name == pipeline_name)
                .first()
                or db.query(PipelinePermission.id)
                .filter(PipelinePermission.pipeline_name == pipeline_name)
                .first()
            )
            if not name_in_use:
                # First time this pipeline is run/created - grant ownership
                # to whoever created it. Global roles do not matter here:
                # per-pipeline ownership governs pipeline access.
                new_perm = PipelinePermission(
                    pipeline_name=pipeline_name,
                    user_id=user.id,
                    permission_level=PermissionLevel.OWNER,
                )
                db.add(new_perm)
                try:
                    db.commit()
                except IntegrityError:
                    # Lost a concurrent first-claim race, or this user
                    # already holds a row: re-read before deciding.
                    db.rollback()
                else:
                    logger.info(
                        "Granted owner permission to user %s for pipeline %s",
                        user.id,
                        pipeline_name,
                    )
                    return
                own_row = (
                    db.query(PipelinePermission.id)
                    .filter(
                        PipelinePermission.pipeline_name == pipeline_name,
                        PipelinePermission.user_id == user.id,
                    )
                    .first()
                )
                if own_row:
                    # Same-user concurrent retry won elsewhere; proceed.
                    return

        # No grant at all: 404 (not 403) so the existence of another
        # user's pipeline/run cannot be probed via the status code.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pipeline run or pipeline not found, or access denied.",
        )

    if permission.permission_level not in required_levels:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"User lacks required permissions ({', '.join(required_levels)}) to perform this action on pipeline '{pipeline_name}'",
        )


def _check_run_permission(
    db: Session,
    user: User,
    pipeline_run: PipelineRun,
    required_levels: list[str],
) -> None:
    """Verify access to a concrete run before falling back to pipeline-name grants."""
    if user.role == "admin":
        return
    if str(pipeline_run.user_id) == str(user.id):
        return
    _check_pipeline_permission(db, user, pipeline_run.name, required_levels)


def _resolve_owned_filename(root, name: str | None):
    """Literal, traversal-safe join of a recorded artifact name under root.

    Returns the resolved path when ``name`` is a plain filename recorded on
    this run's own save steps and the file exists under ``root``; otherwise
    returns None. Recorded names are used literally -- never as glob
    patterns -- so attacker-controlled names cannot match other tenants'
    files in the shared directory.
    """
    if not isinstance(name, str):
        return None
    candidate = name.strip()
    if not candidate or candidate in {".", ".."}:
        return None
    if (".." in candidate or "/" in candidate or "\\" in candidate
            or "\x00" in candidate):
        return None
    if any(ch in candidate for ch in "*?[]"):
        # Literal filenames only: never interpret recorded names as patterns.
        return None
    full = root / candidate
    try:
        resolved = full.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    if not resolved.is_file():
        return None
    return resolved


@router.post("/run",
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
    config = get_parsed_pipeline(body.yaml_config)
    # Name validation runs BEFORE any ownership claim (M-089): a request
    # that will fail validation must never squat the pipeline name.
    pipeline_name = _validate_claim_name(body.name or config.name)

    # Idempotency-Key replay short-circuits before any side effect (M-062).
    idem_key = _get_idempotency_key(request)
    idem_row: IdempotencyKey | None = None
    if idem_key is not None:
        action, idem_row = _idempotency_claim(
            db, scope="pipeline_run", key=idem_key, user_id=current_user.id
        )
        if action == "replay":
            assert idem_row is not None and idem_row.response_body is not None
            return RunPipelineResponse(**idem_row.response_body)
        if action == "inflight":
            assert idem_row is not None
            done = _idempotency_wait(db, idem_row)
            if done is not None and done.response_body is not None:
                return RunPipelineResponse(**done.response_body)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotent request still in progress",
            )

    try:
        return _run_validated_and_queued(
            body, config, pipeline_name, request, db, current_user, idem_row
        )
    except Exception:
        # A request that never produced a response must not pin its key:
        # release the placeholder so retries get a fresh attempt (M-062).
        if idem_row is not None:
            _idempotency_release(db, idem_row)
        raise


def _referenced_wasm_ids(config) -> list[str]:
    """Collect wasm_file_id references from a parsed pipeline config."""
    ids: list[str] = []
    for step in getattr(config, "steps", []):
        wid = getattr(step, "wasm_file_id", None)
        if isinstance(wid, str) and wid.strip():
            ids.append(wid.strip())
    return ids


def _validate_wasm_ownership(db: Session, config, user_id) -> None:
    """Reject runs referencing Wasm modules owned by another user (M-077).

    Every ``wasm_file_id`` must resolve through the aiwasm ownership
    helpers: missing and foreign ids both 404 with the same message, so
    module ids cannot be probed across tenants.
    """
    ids = _referenced_wasm_ids(config)
    if not ids:
        return
    from backend.routers.wasm import assert_wasm_ids_owned

    assert_wasm_ids_owned(db, ids, user_id)


def _run_validated_and_queued(
    body,
    config,
    pipeline_name: str,
    request: Request,
    db: Session,
    current_user: User,
    idem_row: IdempotencyKey | None,
) -> RunPipelineResponse:
    """Validate, claim ownership, queue execution, record idempotency."""
    # Verify all referenced file IDs exist in the database and general config
    # is valid
    registered_ids = _owned_file_ids(db, current_user)
    validation_result = _pipeline_parser.validate(config, registered_ids)

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

        # Log all validation errors to Docker logs for debugging
        error_details = [
            {
                "step_name": e.step_name,
                "field": e.field,
                "message": e.message,
                "suggestion": e.suggestion,
            }
            for e in validation_result.errors
        ]
        logger.warning(
            "400 Bad Request on POST /api/v1/pipelines/run - YAML validation failed"
        )
        logger.warning("Validation errors: %s", error_details)

        # Otherwise, return all validation errors as 400
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Pipeline configuration is invalid",
                "errors": error_details,
            },
        )

    # Pre-flight schema check: simulate column flow to catch mismatches early
    schema_errors = collect_schema_validation_errors(config, db)
    if schema_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "message": "Schema mismatch detected before execution",
                "errors": serialize_validation_errors(schema_errors),
            },
        )

    # Ownership is claimed only after the request is known-valid (M-089).
    # Wasm module ownership is validated here too: a run referencing
    # another tenant's module must 404 before any ownership claim (M-077).
    _validate_wasm_ownership(db, config, current_user.id)
    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner"], grant_owner=True
    )

    run_resp = _create_and_queue_pipeline_run(
        yaml_config=body.yaml_config,
        name=body.name,
        parsed_config=config,
        request=request,
        db=db,
        current_user=current_user,
    )
    if idem_row is not None:
        _idempotency_complete(
            db,
            idem_row,
            status_code=status.HTTP_202_ACCEPTED,
            body=run_resp.model_dump(mode="json"),
            resource_id=run_resp.run_id,
        )
    return run_resp


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
    config = get_parsed_pipeline(yaml_config)
    # Name validation runs BEFORE any ownership claim (M-089).
    pipeline_name = _validate_claim_name(name or config.name)

    # Idempotency-Key replay short-circuits before any side effect (M-062).
    idem_key = _get_idempotency_key(request)
    idem_row: IdempotencyKey | None = None
    if idem_key is not None:
        action, idem_row = _idempotency_claim(
            db, scope="pipeline_run", key=idem_key, user_id=current_user.id
        )
        if action == "replay":
            assert idem_row is not None and idem_row.response_body is not None
            return RunPipelineResponse(**idem_row.response_body)
        if action == "inflight":
            assert idem_row is not None
            done = _idempotency_wait(db, idem_row)
            if done is not None and done.response_body is not None:
                return RunPipelineResponse(**done.response_body)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotent request still in progress",
            )

    registered_ids = _owned_file_ids(db, current_user)
    validation_result = _pipeline_parser.validate(config, registered_ids)
    try:
        return _run_legacy_validated_and_queued(
            yaml_config, name, config, validation_result,
            pipeline_name, request, db, current_user, idem_row,
        )
    except Exception:
        # A request that never produced a response must not pin its key (M-062).
        if idem_row is not None:
            _idempotency_release(db, idem_row)
        raise


def _run_legacy_validated_and_queued(
    yaml_config: str,
    name: str | None,
    config,
    validation_result,
    pipeline_name: str,
    request: Request,
    db: Session,
    current_user: User,
    idem_row: IdempotencyKey | None,
) -> RunPipelineResponse:
    """Validate, claim ownership, queue execution, record idempotency."""
    if not validation_result.is_valid:
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
    # Ownership is claimed only after the request is known-valid (M-089).
    # Wasm module ownership is validated here too (M-077).
    _validate_wasm_ownership(db, config, current_user.id)
    _check_pipeline_permission(
        db, current_user, pipeline_name, ["owner", "runner"], grant_owner=True
    )
    response_model = _create_and_queue_pipeline_run(
        yaml_config=yaml_config,
        name=name,
        parsed_config=config,
        request=request,
        db=db,
        current_user=current_user,
    )
    if idem_row is not None:
        _idempotency_complete(
            db,
            idem_row,
            status_code=status.HTTP_202_ACCEPTED,
            body=response_model.model_dump(mode="json"),
            resource_id=response_model.run_id,
        )
    return response_model


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
    status_filter: str | None = None,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> PipelineRunListResponse:
    """List pipeline runs with pagination, ordered by creation time."""
    page = max(1, page)
    limit = max(1, min(limit, 100))

    query = db.query(PipelineRun).filter(
        PipelineRun.user_id == current_user.id)
    if status_filter:
        try:
            ps = PipelineStatus(status_filter.upper())
            query = query.filter(PipelineRun.status == ps)
        except ValueError:
            pass

    total = query.count()
    runs = (
        query.options(
            selectinload(PipelineRun.step_results),
            selectinload(PipelineRun.healing_attempts),
        )
        .order_by(PipelineRun.created_at.desc())
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
        db.query(
            func.count(
                PipelineRun.id)) .filter(
            PipelineRun.status.in_(
                [
                    PipelineStatus.COMPLETED,
                    PipelineStatus.HEALED]),
            PipelineRun.user_id == current_user.id,
        ) .scalar() or 0)
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
    avg_duration = (
        db.query(
            func.avg(
                func.extract("epoch", PipelineRun.completed_at - PipelineRun.started_at) * 1000
            )
        )
        .filter(
            PipelineRun.user_id == current_user.id,
            PipelineRun.started_at.isnot(None),
            PipelineRun.completed_at.isnot(None),
        )
        .scalar()
        or 0
    )
    return {
        "total_runs": total_runs,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "success_rate": round(
            completed / total_runs * 100,
            1) if total_runs > 0 else 0,
        "total_files": total_files,
        "avg_duration_ms": round(float(avg_duration)) if avg_duration else 0,
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
    logger.debug("User %s accessing run %s", current_user.id, run_id)
    _validate_uuid_format(run_id)
    pipeline_run = (db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first())
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(
        db, current_user, pipeline_run, ["owner", "runner", "viewer"]
    )

    return _run_to_response(pipeline_run)


@router.get(
    "/{run_id}/healing-attempts",
    response_model=list[HealingAttemptResponse],
    summary="List healing attempts for a run",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def list_healing_attempts(
    run_id: str,
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> list[HealingAttemptResponse]:
    """Return all healing attempts for a pipeline run."""
    _validate_uuid_format(run_id)
    pipeline_run = (db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first())
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(
        db, current_user, pipeline_run, ["owner", "runner", "viewer"]
    )

    attempts = (
        db.query(HealingAttempt)
        .filter(HealingAttempt.pipeline_run_id == pipeline_run.id)
        .order_by(HealingAttempt.attempt_number.asc())
        .all()
    )
    return [_healing_attempt_to_response(a) for a in attempts]


@router.get(
    "/{run_id}/healing-attempts/{attempt_number}",
    response_model=HealingAttemptResponse,
    summary="Get one healing attempt",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_healing_attempt(
    run_id: str,
    attempt_number: int,
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> HealingAttemptResponse:
    """Return one healing attempt by run ID and attempt number."""
    _validate_uuid_format(run_id)
    if attempt_number < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="attempt_number must be >= 1",
        )

    pipeline_run = (db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first())
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(
        db, current_user, pipeline_run, ["owner", "runner", "viewer"]
    )

    attempt = (
        db.query(HealingAttempt)
        .filter(
            HealingAttempt.pipeline_run_id == pipeline_run.id,
            HealingAttempt.attempt_number == attempt_number,
        )
        .first()
    )
    if attempt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Healing attempt {attempt_number} not found for run '{run_id}'",
        )
    return _healing_attempt_to_response(attempt)


@router.get(
    "/{run_id}/healing-history",
    summary="Get healing history for a run",
)
@limiter.limit(settings.RATE_LIMIT_READ)
def get_healing_history(
    run_id: str,
    request: Request,
    response: Response,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return healing attempts wrapped in a run-scoped payload."""
    attempts = list_healing_attempts(
        run_id=run_id,
        request=request,
        response=response,
        db=db,
        current_user=current_user,
    )
    return {"run_id": run_id, "healing_attempts": [
        attempt.model_dump() for attempt in attempts]}


def _ensure_utc(dt):
    """Attach UTC tzinfo to naive datetimes from SQLite."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _iter_chunks(file_obj):
    while True:
        chunk = file_obj.read(8192)
        if not chunk:
            break
        yield chunk


def _download_s3_export_object(s3_key: str):
    """Download one S3 export object as a streaming response.

    Returns None when the object is missing or unreadable so the caller
    can try the next candidate key.
    """
    from fastapi.responses import StreamingResponse

    from backend.services.storage_service import storage_service

    try:
        if not storage_service.exists(s3_key):
            return None
        file_obj = storage_service.download(s3_key)
    except Exception as e:
        logger.warning("Failed to download %s from S3: %s", s3_key, e)
        return None
    media_type = "text/csv" if s3_key.endswith(".csv") else "application/json"
    return StreamingResponse(
        _iter_chunks(file_obj),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{os.path.basename(s3_key)}"'
        },
    )


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
                trace_id=sr.trace_id,
                span_id=sr.span_id,
                started_at=sr.started_at.isoformat() if sr.started_at else None,
                completed_at=sr.completed_at.isoformat() if sr.completed_at else None,
                engine=sr.engine,
                download_url=sr.download_url,
                output_filename=sr.output_filename,
                output_format=sr.output_format,
                output_size_bytes=sr.output_size_bytes,
            )
            for sr in pipeline_run.step_results
        ],
        healing_attempts=[
            _healing_attempt_to_response(attempt)
            for attempt in sorted(
                pipeline_run.healing_attempts,
                key=lambda a: a.attempt_number,
            )
        ],
    )


def _healing_attempt_to_response(
        attempt: HealingAttempt) -> HealingAttemptResponse:
    """Convert a HealingAttempt ORM model to an API response."""
    return HealingAttemptResponse(
        id=str(
            attempt.id),
        attempt_number=attempt.attempt_number,
        status=attempt.status.value,
        pipeline_name=attempt.pipeline_name,
        failed_step=attempt.failed_step,
        error_type=attempt.error_type,
        error_message=attempt.error_message,
        old_schema=attempt.old_schema,
        new_schema=attempt.new_schema,
        removed_columns=attempt.removed_columns,
        added_columns=attempt.added_columns,
        renamed_candidates=attempt.renamed_candidates,
        gemini_patch=attempt.gemini_patch,
        sandbox_result=attempt.sandbox_result,
        applied=attempt.applied,
        confidence=float(
            attempt.confidence) if attempt.confidence is not None else None,
        healed_at=_ensure_utc(
            attempt.healed_at),
        classification_reason=attempt.classification_reason,
        ai_valid=attempt.ai_valid,
        ai_error=attempt.ai_error,
        parser_valid=attempt.parser_valid,
        sandbox_passed=attempt.sandbox_passed,
        validation_errors=attempt.validation_errors,
        validation_warnings=attempt.validation_warnings,
        diff_lines=attempt.diff_lines,
        created_at=_ensure_utc(
            attempt.created_at),
        completed_at=_ensure_utc(
            attempt.completed_at),
    )


def _status_cache_key(run_id: str) -> str:
    return f"pipeline_progress:last:{run_id}"


@router.post(
    "/{run_id}/cancel",
    summary="Cancel a running pipeline",
    description="Cancel a PENDING, RUNNING, HEALING or streaming pipeline run and revoke its Celery task.",
)
def cancel_pipeline_run(
    run_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Cancel a pipeline run by setting status to CANCELLED and revoking the task."""
    _validate_uuid_format(run_id)
    pipeline_run = (db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first())
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(db, current_user, pipeline_run, ["owner", "runner"])

    # HEALING runs hold a live worker task (autonomous healing executes
    # synchronously inside execute_pipeline_task), so they are cancellable
    # like any other in-flight state (M-152). Revoke SIGTERMs the worker;
    # the CANCELLED flip below sticks because the worker is gone.
    if pipeline_run.status not in (
            PipelineStatus.PENDING,
            PipelineStatus.RUNNING,
            PipelineStatus.HEALING,
            PipelineStatus.STREAMING_ACTIVE,
            PipelineStatus.STREAMING_PAUSED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel pipeline with status '{pipeline_run.status.value}'",
        )

    # Revoke the Celery task
    from backend.celery_app import celery_app as _celery_app

    task_id = pipeline_run.celery_task_id or run_id
    try:
        _celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    except (KombuOperationalError, RedisError, OSError) as exc:
        logger.warning(
            "Failed to revoke task during cancellation for run_id=%s task_id=%s: %s",
            run_id,
            task_id,
            exc,
        )

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
    signed_cancel_payload = sign_sse_payload(cancel_payload)
    try:
        get_pubsub_redis().publish(
            f"pipeline_progress:{run_id}",
            orjson.dumps(signed_cancel_payload),
        )
        get_cache_redis().setex(
            _status_cache_key(run_id), 3600, orjson.dumps(signed_cancel_payload)
        )
    except RedisError:
        logger.warning(
            "Failed to publish cancellation SSE event for run_id=%s",
            run_id)

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
    config = get_parsed_pipeline(body.yaml_config)

    if step_index < 0 or step_index >= len(config.steps):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"step_index {step_index} out of range (0..{len(config.steps) - 1})",
        )

    plan = generate_execution_plan(
        body.yaml_config, db,
        user_id=current_user.id, is_admin=current_user.role == "admin",
    )

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
        "total_steps": len(
            config.steps),
        "step_preview": step_info,
        "note": "Full sample data preview requires pipeline execution. Use /plan for detailed estimates.",
    }


@router.get(
    "/{run_id}/download",
    summary="Get download URL for pipeline run output",
    description=(
        "Returns a fresh presigned download URL (valid 48 hours) for the "
        "output file produced by a successful pipeline run."
    ),
)
def get_run_download_url(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get a fresh presigned download URL for the run's output file."""
    _validate_uuid_format(run_id)
    pipeline_run = (
        db.query(PipelineRun)
        .filter(PipelineRun.id == _as_uuid(run_id))
        .first()
    )
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(db, current_user, pipeline_run, ["owner", "runner"])

    eligible = {PipelineStatus.COMPLETED, PipelineStatus.HEALED}
    if pipeline_run.status not in eligible:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Run has not completed successfully (status: {pipeline_run.status.value})",
        )

    save_steps = sorted(
        [sr for sr in pipeline_run.step_results
         if sr.step_type == "save" and sr.output_object_name],
        key=lambda s: s.step_index,
        reverse=True,
    )

    if not save_steps:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No output file found for this run",
        )

    step_result = save_steps[0]

    fresh_url = step_result.download_url
    if step_result.output_object_name:
        try:
            from backend.execution.steps.save_step import refresh_download_url
            fresh_url = refresh_download_url(step_result.output_object_name)
        except Exception as exc:
            logger.warning(
                "Could not refresh presigned URL, using stored URL: %s", exc)

    return {
        "run_id": run_id,
        "filename": step_result.output_filename,
        "format": step_result.output_format,
        "size_bytes": step_result.output_size_bytes,
        "row_count": step_result.row_count_out,
        "download_url": fresh_url,
        "expires_in_hours": 48,
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
    """Export/download the output file from a completed pipeline run.

    Supports both local filesystem and S3/MinIO storage backends.
    When STORAGE_TYPE=s3, returns a presigned S3 URL for direct download.
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    _validate_uuid_format(run_id)
    pipeline_run = (db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid(run_id)).first())
    if pipeline_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run '{run_id}' not found",
        )

    _check_run_permission(db, current_user, pipeline_run, ["owner", "runner"])

    if pipeline_run.status not in (
            PipelineStatus.COMPLETED,
            PipelineStatus.HEALED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Pipeline run is not completed (status: {pipeline_run.status.value})",
        )

    # Get all save step results to find stored paths (if available)
    save_results = [
        sr for sr in pipeline_run.step_results if sr.step_type == "save"]

    # Resolve local artifacts belonging to THIS run only. No globbing: every
    # candidate is either an exact artifact name recorded on this run's own
    # save steps, or prefixed with this run's validated UUID -- both joined
    # literally under UPLOAD_DIR with symlink-aware confinement. Attacker
    # controlled save filenames / step names can therefore never match
    # another tenant's files in the shared directory.
    output_dir = settings.UPLOAD_DIR
    root = Path(output_dir).resolve()

    output_files: list[str] = []
    seen: set[str] = set()

    def _add_owned(name: str | None) -> None:
        resolved = _resolve_owned_filename(root, name)
        if resolved is None:
            return
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            output_files.append(key)

    # Exact artifact names recorded on this run's own save steps.
    for sr in save_results:
        _add_owned(sr.output_filename)

    # Legacy convention: artifacts prefixed with this run's id. run_id was
    # validated as a UUID above, so the prefix is run-scoped; matching uses
    # literal string comparison, never the glob module.
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        entries = []
    for entry in entries:
        if not entry.is_file():
            continue
        if not entry.name.startswith(run_id):
            continue
        if not entry.name.endswith((".csv", ".json")):
            continue
        try:
            resolved_entry = entry.resolve()
        except OSError:
            continue
        if resolved_entry != root and root not in resolved_entry.parents:
            continue
        key = str(resolved_entry)
        if key not in seen:
            seen.add(key)
            output_files.append(key)

    if output_files:
        output_path = Path(output_files[0])
        media_type = "text/csv" if output_path.suffix == ".csv" else "application/json"

        return FileResponse(
            path=str(output_path),
            media_type=media_type,
            filename=output_path.name,
        )

    if settings.STORAGE_TYPE == "s3":
        s3_keys = [
            sr.output_object_name for sr in save_results
            if sr.output_object_name
        ]
        if not s3_keys:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Could not determine output file path",
            )

        try:
            candidates = [
                _download_s3_export_object(s3_key) for s3_key in s3_keys
            ]
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("S3 export error for run_id=%s", run_id)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to download file from S3 storage: {e!s}",
            ) from e
        found = next((r for r in candidates if r is not None), None)
        if found is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Output file not found in S3 storage",
            )
        return found

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="No output file found for this pipeline run",
    )


@router.get(
    "/{run_id}/timing",
    summary="Get step execution timing data",
    description="Returns per-step start/end/duration and trace IDs for Gantt chart rendering.",
)
def get_run_timing(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    _validate_uuid_format(run_id)

    run = db.query(PipelineRun).filter(PipelineRun.id == _as_uuid(run_id)).first()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    _check_run_permission(db, current_user, run, ["owner", "runner", "viewer"])

    steps = (
        db.query(StepResult)
        .filter(StepResult.pipeline_run_id == run.id)
        .order_by(StepResult.step_index)
        .all()
    )

    return {
        "run_id": str(run.id),
        "status": run.status.value,
        "jaeger_ui_url": settings.JAEGER_UI_URL,
        "steps": [
            {
                "step_index": s.step_index,
                "step_name": s.step_name,
                "step_type": s.step_type,
                "status": s.status.value,
                "rows_in": s.rows_in,
                "rows_out": s.rows_out,
                "duration_ms": s.duration_ms,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "trace_id": s.trace_id,
                "span_id": s.span_id,
                "engine": s.engine,
            }
            for s in steps
        ],
        "total_duration_ms": run.duration_ms,
    }


from fastapi import Body as _Body


@legacy_runs_router.post("/estimate")
def estimate_run_cost(
    pipeline_yaml: str = _Body(...),
    file_ids: list[str] = _Body(default_factory=list),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    from backend.cost.estimator import estimate_pipeline_cost

    owned = _owned_file_ids(db, current_user)
    estimate = estimate_pipeline_cost(
        pipeline_yaml=pipeline_yaml,
        file_ids=[fid for fid in file_ids if str(fid) in owned],
        db=db,
    )

    total_seconds = estimate.total_ms / 1000
    if total_seconds < 60:
        duration_human = f"{total_seconds:.1f}s"
    else:
        duration_human = f"{int(total_seconds // 60)}m {int(total_seconds % 60)}s"

    return {
        "total_ms": estimate.total_ms,
        "duration_human": duration_human,
        "peak_memory_mb": estimate.peak_memory_mb,
        "confidence": estimate.confidence,
        "optimization_tip": estimate.optimization_tip,
        "data_points_used": estimate.data_points_used,
        "steps": [
            {
                "step_name": s.step_name,
                "step_type": s.step_type,
                "engine": s.engine,
                "predicted_ms": s.predicted_ms,
                "confidence": round(s.confidence * 100, 1),
                "row_in_est": s.row_in_est,
                "row_out_est": s.row_out_est,
            }
            for s in estimate.step_estimates
        ],
    }
