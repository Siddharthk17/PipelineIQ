"""Data contract API endpoints.

Provides CRUD operations for pipeline data contract definitions plus
read-only access to contract violations detected during pipeline execution.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import (
    ContractViolationRecord,
    PermissionLevel,
    PipelineContract,
    PipelinePermission,
    PipelineRun,
    User,
)
from backend.models.data_contract import IdempotencyKey
from backend.schemas import (
    ContractCreateRequest,
    ContractDefResponse,
    ContractListResponse,
    ContractStatusResponse,
    ContractUpdateRequest,
)
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/contracts", tags=["contracts"])

# Idempotency-Key polling budget for a concurrent duplicate create that is
# still in flight (seconds). Sequential replays never wait.
_IDEMPOTENT_INFLIGHT_TIMEOUT_S = 5.0
_IDEMPOTENT_POLL_INTERVAL_S = 0.2

# Upper bound on retries for deactivate-then-insert version races (M-008).
_VERSION_WRITE_MAX_ATTEMPTS = 3

# A claim placeholder (status_code NULL) older than this never belonged to a
# live request: the duplicate-wait budget is seconds, so a day-old row is a
# leftover of a crashed request. Such rows are reclaimed opportunistically
# at claim time so a dead key cannot pin its (scope, key, user) forever and
# the table cannot grow without bound. No migration, no TTL column (M-062).
_STALE_PLACEHOLDER_TTL = timedelta(hours=24)

# Bound on rows swept per claim so one request never pays for the whole
# table; leftovers are picked up by later claims.
_STALE_GC_BATCH_LIMIT = 100


def _placeholder_is_stale(row: IdempotencyKey) -> bool:
    """True when `row` is an uncompleted claim older than the TTL."""
    if row.status_code is not None or row.created_at is None:
        return False
    seen = row.created_at
    if seen.tzinfo is None:
        # SQLite returns naive datetimes; PG returns aware. Normalize so
        # the comparison is valid on either backend.
        seen = seen.replace(tzinfo=UTC)
    return seen < datetime.now(UTC) - _STALE_PLACEHOLDER_TTL


def _idempotency_gc_stale(db: Session, scope: str) -> None:
    """Delete stale claim placeholders for `scope`, best-effort (M-062)."""
    try:
        candidates = (
            db.query(IdempotencyKey)
            .filter(
                IdempotencyKey.scope == scope,
                IdempotencyKey.status_code.is_(None),
            )
            .limit(_STALE_GC_BATCH_LIMIT)
            .all()
        )
        for candidate in candidates:
            if _placeholder_is_stale(candidate):
                db.delete(candidate)
        db.commit()
    except Exception:  # never fail a claim because housekeeping failed
        logger.warning("Stale idempotency-placeholder GC failed",
                       exc_info=True)
        db.rollback()


def _ensure_pipeline_contract_access(
    db: Session,
    current_user: User,
    pipeline_name: str,
    required_levels: set[str],
    *,
    allow_new: bool = False,
) -> None:
    if current_user.role == "admin":
        return
    # NOTE: pipeline identity is name-based. A run that merely shares a
    # name with someone else's pipeline grants nothing — ownership comes
    # only from the contract record itself or an explicit permission row.
    if (
        db.query(PipelineContract)
        .filter(
            PipelineContract.pipeline_name == pipeline_name,
            PipelineContract.user_id == current_user.id,
        )
        .first()
    ):
        return
    permission = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == current_user.id,
        )
        .first()
    )
    if permission and permission.permission_level in required_levels:
        return
    if allow_new and not (
        db.query(PipelineRun).filter(PipelineRun.name == pipeline_name).first()
        or db.query(PipelineContract)
        .filter(PipelineContract.pipeline_name == pipeline_name)
        .first()
        or db.query(PipelinePermission)
        .filter(PipelinePermission.pipeline_name == pipeline_name)
        .first()
    ):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this pipeline")


def _contract_query(db: Session, current_user: User):
    query = db.query(PipelineContract)
    if current_user.role != "admin":
        query = query.filter(PipelineContract.user_id == current_user.id)
    return query


def _ordered_versions(query):
    """Deterministic newest-first ordering (M-008): version ties break by id."""
    return query.order_by(
        PipelineContract.version.desc(), PipelineContract.id.desc()
    )


def _validate_consumers(
    db: Session,
    current_user: User,
    consumers: list[str] | None,
) -> None:
    """Reject consumer entries naming pipelines the caller does not own (M-076).

    A breach with severity=block makes workers pause every active schedule
    whose pipeline_name appears in ``consumers``. Without this check any
    contract owner could block another tenant's schedules by name. Entries
    that do not match a known pipeline (e.g. e-mail/Slack notification
    targets) are allowed; entries that match an existing pipeline require
    owner-level access (own contract, OWNER grant, or admin).
    """
    if current_user.role == "admin":
        return
    bad: list[str] = []
    for entry in consumers or []:
        if not isinstance(entry, str) or not entry.strip():
            continue
        name = entry.strip()
        in_use = (
            db.query(PipelineRun.id)
            .filter(PipelineRun.name == name)
            .first()
            or db.query(PipelineContract.id)
            .filter(PipelineContract.pipeline_name == name)
            .first()
            or db.query(PipelinePermission.id)
            .filter(PipelinePermission.pipeline_name == name)
            .first()
        )
        if not in_use:
            continue
        own_contract = (
            db.query(PipelineContract.id)
            .filter(
                PipelineContract.pipeline_name == name,
                PipelineContract.user_id == current_user.id,
            )
            .first()
        )
        permission = (
            db.query(PipelinePermission)
            .filter(
                PipelinePermission.pipeline_name == name,
                PipelinePermission.user_id == current_user.id,
            )
            .first()
        )
        if own_contract or (
            permission is not None
            and permission.permission_level == PermissionLevel.OWNER
        ):
            continue
        bad.append(entry)
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Consumers must not name pipelines you do not own: "
                + ", ".join(sorted(set(bad)))
            ),
        )
def _validate_contract_yaml(yaml_content: str) -> dict:
    """Reject unparseable/non-mapping contract YAML at write time (M-088).

    Invalid YAML must never be stored as the active contract: it would
    shadow the last good version while only failing at run time.
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Contract YAML is invalid: {exc}",
        ) from exc
    if not isinstance(data, dict) or not data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Contract YAML must be a non-empty mapping",
        )
    return data


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


def _idempotency_lookup(
    db: Session, *, scope: str, key: str, user_id
) -> IdempotencyKey | None:
    return (
        db.query(IdempotencyKey)
        .filter(
            IdempotencyKey.scope == scope,
            IdempotencyKey.key == key,
            IdempotencyKey.user_id == user_id,
        )
        .first()
    )


def _idempotency_claim(
    db: Session, *, scope: str, key: str, user_id
) -> tuple[str, IdempotencyKey | None]:
    """Claim an idempotency key or return the stored outcome (M-062).

    Returns ("owner", row) when this request won the key, ("replay", row)
    when a completed response is stored, or ("inflight", row) when another
    request is still producing the response. The unique constraint makes
    concurrent duplicate claims collapse to a single owner.
    """
    _idempotency_gc_stale(db, scope)
    existing = _idempotency_lookup(db, scope=scope, key=key, user_id=user_id)
    if existing is not None:
        if existing.status_code is not None:
            return "replay", existing
        if _placeholder_is_stale(existing):
            # Leftover of a crashed request: reclaim it instead of 409ing
            # forever on a key whose owner will never come back (M-062).
            try:
                db.delete(existing)
                db.commit()
            except Exception:
                logger.warning("Failed to reclaim stale idempotency "
                               "placeholder", exc_info=True)
                db.rollback()
                return "inflight", existing
            existing = None
        else:
            return "inflight", existing
    if existing is None:
        row = IdempotencyKey(
            scope=scope,
            key=key,
            user_id=user_id,
            status_code=None,
            response_body=None,
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            existing = _idempotency_lookup(
                db, scope=scope, key=key, user_id=user_id)
            if existing is not None and existing.status_code is not None:
                return "replay", existing
            return "inflight", existing
        db.refresh(row)
        return "owner", row


def _idempotency_wait(
    db: Session, row: IdempotencyKey
) -> IdempotencyKey | None:
    """Wait briefly for an in-flight duplicate to complete (M-062)."""
    deadline = time.monotonic() + _IDEMPOTENT_INFLIGHT_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(_IDEMPOTENT_POLL_INTERVAL_S)
        db.expire(row)
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


def _create_contract_version(
    db: Session,
    *,
    pipeline_name: str,
    yaml_content: str,
    severity: str,
    consumers: list[str],
    user_id,
) -> PipelineContract:
    """Deactivate-then-insert a new contract version atomically (M-008).

    The deactivate UPDATE and the INSERT run in one transaction guarded by
    the partial unique index on the active (pipeline_name) and the
    (pipeline_name, version) unique constraint. On a concurrent-write
    IntegrityError the version base is recomputed from MAX(version) and
    the attempt retried, so two racing writers produce versions N+1/N+2
    with exactly one active row instead of duplicates.
    """
    last_exc: Exception | None = None
    for _ in range(_VERSION_WRITE_MAX_ATTEMPTS):
        latest_version = (
            db.query(PipelineContract.version)
            .filter(PipelineContract.pipeline_name == pipeline_name)
            .order_by(PipelineContract.version.desc())
            .limit(1)
            .scalar()
        )
        # Single-statement deactivation of every active row for the pipeline.
        db.query(PipelineContract).filter(
            PipelineContract.pipeline_name == pipeline_name,
            PipelineContract.is_active.is_(True),
        ).update(
            {PipelineContract.is_active: False},
            synchronize_session=False,
        )
        contract = PipelineContract(
            pipeline_name=pipeline_name,
            version=(latest_version + 1) if latest_version is not None else 1,
            yaml_content=yaml_content,
            severity=severity,
            consumers=consumers,
            user_id=user_id,
            is_active=True,
        )
        db.add(contract)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            last_exc = exc
            continue
        db.refresh(contract)
        return contract
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "Concurrent contract write conflict for pipeline "
            f"'{pipeline_name}'; please retry"
        ),
    ) from last_exc


def _to_def_response(contract: PipelineContract) -> ContractDefResponse:
    return ContractDefResponse(
        id=str(contract.id),
        pipeline_name=contract.pipeline_name,
        version=contract.version,
        yaml_content=contract.yaml_content,
        severity=contract.severity.value if hasattr(contract.severity, "value") else str(contract.severity),
        consumers=contract.consumers or [],
        is_active=contract.is_active,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


# Contract definition CRUD


@router.get("/pipelines/{pipeline_name}", response_model=ContractListResponse)
def list_contracts(
    pipeline_name: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all contract definitions for a pipeline."""
    _ensure_pipeline_contract_access(
        db, current_user, pipeline_name, {"owner", "runner", "viewer"}
    )
    # M-085: bound the list (clamped like list_pipeline_runs, hard cap
    # 200 like catalog). total stays the full count.
    page = max(1, min(page, 1_000_000))
    limit = max(1, min(limit, 200))
    base_query = _contract_query(db, current_user).filter(
        PipelineContract.pipeline_name == pipeline_name
    )
    total = base_query.count()
    contracts = (
        _ordered_versions(base_query)
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return ContractListResponse(
        pipeline_name=pipeline_name,
        contracts=[
            ContractDefResponse(
                id=str(c.id),
                pipeline_name=c.pipeline_name,
                version=c.version,
                yaml_content=c.yaml_content,
                severity=c.severity.value if hasattr(c.severity, "value") else str(c.severity),
                consumers=c.consumers or [],
                is_active=c.is_active,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in contracts
        ],
        total=total,
    )


@router.post(
    "/pipelines/{pipeline_name}",
    response_model=ContractDefResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_contract(
    pipeline_name: str,
    body: ContractCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new data contract definition for a pipeline."""
    _ensure_pipeline_contract_access(
        db, current_user, pipeline_name, {"owner"}, allow_new=True
    )
    _validate_contract_yaml(body.yaml_content)
    _validate_consumers(db, current_user, body.consumers)

    idem_key = _get_idempotency_key(request)
    idem_row: IdempotencyKey | None = None
    if idem_key is not None:
        action, idem_row = _idempotency_claim(
            db, scope="contract_create", key=idem_key, user_id=current_user.id
        )
        if action == "replay":
            assert idem_row is not None and idem_row.response_body is not None
            return ContractDefResponse(**idem_row.response_body)
        if action == "inflight":
            assert idem_row is not None
            done = _idempotency_wait(db, idem_row)
            if done is not None and done.response_body is not None:
                return ContractDefResponse(**done.response_body)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotent request still in progress",
            )

    try:
        contract = _create_contract_version(
            db,
            pipeline_name=pipeline_name,
            yaml_content=body.yaml_content,
            severity=body.severity,
            consumers=body.consumers,
            user_id=current_user.id,
        )
    except HTTPException as exc:
        # A version race with the same idempotency key means the winner
        # already stored the outcome — replay it instead of 409.
        if idem_key is not None and exc.status_code == status.HTTP_409_CONFLICT:
            stored = _idempotency_lookup(
                db, scope="contract_create", key=idem_key,
                user_id=current_user.id,
            )
            if stored is not None and stored.response_body is not None:
                return ContractDefResponse(**stored.response_body)
        if idem_row is not None:
            _idempotency_release(db, idem_row)
        raise
    except Exception:
        # A non-HTTP failure (DB blip mid-commit, bug) must not pin the key
        # forever: release the placeholder so a retry gets a fresh attempt
        # instead of 409 "still in progress" until the end of time (M-062).
        if idem_row is not None:
            _idempotency_release(db, idem_row)
        raise

    response = _to_def_response(contract)
    if idem_row is not None:
        _idempotency_complete(
            db,
            idem_row,
            status_code=status.HTTP_201_CREATED,
            body=response.model_dump(mode="json"),
            resource_id=str(contract.id),
        )
    return response


# NOTE (M-030): literal sub-routes MUST be registered before
# /pipelines/{pipeline_name}/{contract_id} — otherwise "status"/"breaches"
# are captured as a contract_id and the endpoints are unreachable.
@router.get(
    "/pipelines/{pipeline_name}/status",
    response_model=ContractStatusResponse,
)
def get_contract_status(
    pipeline_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the status of a pipeline's active contract against its latest run."""
    _ensure_pipeline_contract_access(
        db, current_user, pipeline_name, {"owner", "runner", "viewer"}
    )
    active = (
        _ordered_versions(
            _contract_query(db, current_user).filter(
                PipelineContract.pipeline_name == pipeline_name,
                PipelineContract.is_active.is_(True),
            )
        )
        .first()
    )

    last_run_query = db.query(PipelineRun).filter(PipelineRun.name == pipeline_name)
    if current_user.role != "admin":
        last_run_query = last_run_query.filter(PipelineRun.user_id == current_user.id)
    last_run = last_run_query.order_by(
        PipelineRun.created_at.desc(), PipelineRun.id.desc()
    ).first()

    total_violations = 0
    if active and last_run:
        total_violations = (
            db.query(ContractViolationRecord)
            .filter(ContractViolationRecord.run_id == last_run.id)
            .count()
        )

    return ContractStatusResponse(
        pipeline_name=pipeline_name,
        has_contract=active is not None,
        active_contract_id=str(active.id) if active else None,
        active_contract_version=active.version if active else None,
        last_run_id=str(last_run.id) if last_run else None,
        last_run_status=last_run.status.value if last_run else None,
        total_violations=total_violations,
    )


@router.get("/pipelines/{pipeline_name}/breaches")
def list_pipeline_breaches(
    pipeline_name: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List contract violations for the latest run of a pipeline."""
    _ensure_pipeline_contract_access(
        db, current_user, pipeline_name, {"owner", "runner", "viewer"}
    )
    last_run_query = db.query(PipelineRun).filter(PipelineRun.name == pipeline_name)
    if current_user.role != "admin":
        last_run_query = last_run_query.filter(PipelineRun.user_id == current_user.id)
    last_run = last_run_query.order_by(
        PipelineRun.created_at.desc(), PipelineRun.id.desc()
    ).first()
    if not last_run:
        return {"run_id": None, "total_violations": 0, "violations": []}

    # M-085: bound the list (clamped like list_pipeline_runs, hard cap
    # 200 like catalog). total_violations stays the full count.
    page = max(1, min(page, 1_000_000))
    limit = max(1, min(limit, 200))
    base_query = db.query(ContractViolationRecord).filter(
        ContractViolationRecord.run_id == last_run.id
    )
    total = base_query.count()
    violations = (
        base_query.order_by(
            ContractViolationRecord.step_index,
            ContractViolationRecord.severity.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "run_id": str(last_run.id),
        "total_violations": total,
        "violations": [
            {
                "id": str(v.id),
                "step_name": v.step_name,
                "step_index": v.step_index,
                "step_type": v.step_type,
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in violations
        ],
    }


@router.get(
    "/pipelines/{pipeline_name}/{contract_id}",
    response_model=ContractDefResponse,
)
def get_contract(
    pipeline_name: str,
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get a specific contract definition by ID."""
    validate_uuid_format(contract_id)
    _ensure_pipeline_contract_access(
        db, current_user, pipeline_name, {"owner", "runner", "viewer"}
    )
    contract = (
        _contract_query(db, current_user)
        .filter(
            PipelineContract.id == as_uuid(contract_id),
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not contract:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )
    return ContractDefResponse(
        id=str(contract.id),
        pipeline_name=contract.pipeline_name,
        version=contract.version,
        yaml_content=contract.yaml_content,
        severity=contract.severity.value if hasattr(contract.severity, "value") else str(contract.severity),
        consumers=contract.consumers or [],
        is_active=contract.is_active,
        created_at=contract.created_at,
        updated_at=contract.updated_at,
    )


@router.put(
    "/pipelines/{pipeline_name}/{contract_id}",
    response_model=ContractDefResponse,
)
def update_contract(
    pipeline_name: str,
    contract_id: str,
    body: ContractUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update an existing contract definition (creates a new version)."""
    validate_uuid_format(contract_id)
    _ensure_pipeline_contract_access(db, current_user, pipeline_name, {"owner"})
    _validate_contract_yaml(body.yaml_content)
    _validate_consumers(db, current_user, body.consumers)
    existing = (
        _contract_query(db, current_user)
        .filter(
            PipelineContract.id == as_uuid(contract_id),
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )

    # R2-004: retried PUTs version-spammed (N+1, N+2). Honor
    # Idempotency-Key like create (M-062); claimed after validation so
    # 4xx never holds a placeholder.
    idem_key = _get_idempotency_key(request)
    idem_row: IdempotencyKey | None = None
    if idem_key is not None:
        action, idem_row = _idempotency_claim(
            db, scope="contract_update", key=idem_key,
            user_id=current_user.id,
        )
        if action == "replay":
            assert idem_row is not None and idem_row.response_body is not None
            return ContractDefResponse(**idem_row.response_body)
        if action == "inflight":
            assert idem_row is not None
            done = _idempotency_wait(db, idem_row)
            if done is not None and done.response_body is not None:
                return ContractDefResponse(**done.response_body)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotent request still in progress",
            )

    # New version via the same atomic deactivate-then-insert path as
    # create (M-008): never two actives, never duplicate versions.
    new_contract = _create_contract_version(
        db,
        pipeline_name=pipeline_name,
        yaml_content=body.yaml_content,
        severity=body.severity,
        consumers=body.consumers,
        user_id=current_user.id,
    )

    response = _to_def_response(new_contract)
    if idem_row is not None:
        _idempotency_complete(
            db,
            idem_row,
            status_code=status.HTTP_200_OK,
            body=response.model_dump(mode="json"),
            resource_id=str(new_contract.id),
        )
    return response


@router.delete(
    "/pipelines/{pipeline_name}/{contract_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_contract(
    pipeline_name: str,
    contract_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a contract definition."""
    validate_uuid_format(contract_id)
    _ensure_pipeline_contract_access(db, current_user, pipeline_name, {"owner"})
    contract = (
        _contract_query(db, current_user)
        .filter(
            PipelineContract.id == as_uuid(contract_id),
            PipelineContract.pipeline_name == pipeline_name,
        )
        .first()
    )
    if not contract:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contract {contract_id} not found for pipeline {pipeline_name}",
        )
    db.delete(contract)
    db.commit()
    return None


# Contract violation read endpoints


@router.get("/runs/{run_id}")
def list_run_violations(
    run_id: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all contract violations for a given pipeline run."""
    validate_uuid_format(run_id)
    run = db.query(PipelineRun).filter(
        PipelineRun.id == as_uuid(run_id)).first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run {run_id} not found",
        )
    if current_user.role != "admin" and str(run.user_id) != str(current_user.id):
        _ensure_pipeline_contract_access(
            db, current_user, run.name, {"owner", "runner", "viewer"}
        )

    # M-085: bound the list (clamped like list_pipeline_runs, hard cap
    # 200 like catalog). total_violations stays the full count.
    page = max(1, min(page, 1_000_000))
    limit = max(1, min(limit, 200))
    base_query = db.query(ContractViolationRecord).filter(
        ContractViolationRecord.run_id == as_uuid(run_id)
    )
    total = base_query.count()
    violations = (
        base_query.order_by(
            ContractViolationRecord.step_index,
            ContractViolationRecord.severity.desc(),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "run_id": run_id,
        "total_violations": total,
        "violations": [
            {
                "id": str(v.id),
                "step_name": v.step_name,
                "step_index": v.step_index,
                "step_type": v.step_type,
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in violations
        ],
    }


@router.get("/runs/{run_id}/steps/{step_name}")
def list_step_violations(
    run_id: str,
    step_name: str,
    page: int = 1,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List contract violations for a specific step within a run."""
    validate_uuid_format(run_id)
    run = db.query(PipelineRun).filter(PipelineRun.id == as_uuid(run_id)).first()
    if not run:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Pipeline run {run_id} not found",
        )
    if current_user.role != "admin" and str(run.user_id) != str(current_user.id):
        _ensure_pipeline_contract_access(
            db, current_user, run.name, {"owner", "runner", "viewer"}
        )
    # M-085: bound the list (clamped like list_pipeline_runs, hard cap
    # 200 like catalog). total_violations stays the full count.
    page = max(1, min(page, 1_000_000))
    limit = max(1, min(limit, 200))
    base_query = db.query(ContractViolationRecord).filter(
        ContractViolationRecord.run_id == as_uuid(run_id),
        ContractViolationRecord.step_name == step_name,
    )
    total = base_query.count()
    violations = (
        base_query.order_by(ContractViolationRecord.severity.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "run_id": run_id,
        "step_name": step_name,
        "total_violations": total,
        "violations": [
            {
                "id": str(v.id),
                "column": v.column,
                "rule": v.rule,
                "severity": v.severity,
                "message": v.message,
                "actual": v.actual,
                "expected": v.expected,
            }
            for v in violations
        ],
    }
