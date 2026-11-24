"""Per-pipeline RBAC API endpoints.

Provides fine-grained permission management for individual pipelines,
allowing owners to grant runner or viewer access to other users.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.authz import require_pipeline_owner
from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import (
    PermissionLevel,
    PipelineContract,
    PipelinePermission,
    User,
)
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["permissions"])


class GrantPermissionRequest(BaseModel):
    """Request body to grant a permission on a pipeline."""

    user_id: str = Field(...,
                         description="UUID of the user to grant permission to")
    permission_level: str = Field(
        ..., description="Permission level: 'owner', 'runner', or 'viewer'"
    )

    @field_validator("permission_level")
    @classmethod
    def normalize_permission_level(cls, value: str) -> str:
        """Accept case-insensitive permission levels from clients."""
        return value.strip().lower()


class PermissionResponse(BaseModel):
    """Response for a pipeline permission entry."""

    id: str
    pipeline_name: str
    user_id: str
    permission_level: str
    created_at: str | None = None


def _permission_to_response(perm: PipelinePermission) -> PermissionResponse:
    return PermissionResponse(
        id=str(perm.id),
        pipeline_name=perm.pipeline_name,
        user_id=str(perm.user_id),
        permission_level=perm.permission_level.value
        if hasattr(perm.permission_level, "value")
        else str(perm.permission_level),
        created_at=perm.created_at.isoformat() if perm.created_at else None,
    )


def _require_owner(db: Session, pipeline_name: str, user: User) -> str:
    """Raise 403 if the user is not an owner of the pipeline.

    Delegates to the shared :func:`require_pipeline_owner` helper (M-093)
    and returns the canonical pipeline name. Kept as a thin wrapper for
    backward compatibility with existing importers.
    """
    return require_pipeline_owner(db, user, pipeline_name)


@router.post(
    "/{pipeline_name}/permissions",
    response_model=PermissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Grant a permission on a pipeline",
)
def grant_permission(
    pipeline_name: str,
    body: GrantPermissionRequest,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> PermissionResponse:
    """Grant a user permission on a specific pipeline (owner only)."""
    pipeline_name = _require_owner(db, pipeline_name, current_user)

    try:
        level = PermissionLevel(body.permission_level)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid permission level. Must be 'owner', 'runner', or 'viewer'.",
        ) from exc

    validate_uuid_format(body.user_id)
    target_user = db.query(User).filter(
        User.id == as_uuid(body.user_id)).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target user not found")

    # Re granting the same level is an explicit conflict, not a silent
    # no-op (M-153). Changing the level is an explicit role change and is
    # audited like the initial grant (M-150).
    existing = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == as_uuid(body.user_id),
        )
        .first()
    )
    if existing is not None and existing.permission_level == level:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already holds this permission level on the pipeline.",
        )
    if existing is not None:
        old_level = (
            existing.permission_level.value
            if hasattr(existing.permission_level, "value")
            else str(existing.permission_level)
        )
        existing.permission_level = level
        db.commit()
        db.refresh(existing)
        log_action(
            db,
            "permission_updated",
            user_id=current_user.id,
            resource_type="pipeline_permission",
            resource_id=existing.id,
            details={
                "pipeline_name": pipeline_name,
                "target_user": body.user_id,
                "old_level": old_level,
                "level": body.permission_level,
            },
            request=request,
        )
        return _permission_to_response(existing)

    perm = PipelinePermission(
        pipeline_name=pipeline_name,
        user_id=as_uuid(body.user_id),
        permission_level=level,
    )
    db.add(perm)
    try:
        db.commit()
    except IntegrityError:
        # Lost a concurrent grant race (M-105): another request inserted
        # the same (pipeline, user) row between our check and our insert.
        # Roll back and report an explicit conflict instead of 500ing; the
        # caller retries and then sees the current level via GET.
        db.rollback()
        existing = (
            db.query(PipelinePermission)
            .filter(
                PipelinePermission.pipeline_name == pipeline_name,
                PipelinePermission.user_id == as_uuid(body.user_id),
            )
            .first()
        )
        if existing is None:
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Permission was granted concurrently; fetch the current "
            "grant and retry if a change is still needed.",
        ) from None
    db.refresh(perm)

    log_action(
        db,
        "permission_granted",
        user_id=current_user.id,
        resource_type="pipeline_permission",
        resource_id=perm.id,
        details={
            "pipeline_name": pipeline_name,
            "target_user": body.user_id,
            "level": body.permission_level,
        },
        request=request,
    )

    return _permission_to_response(perm)


@router.get(
    "/{pipeline_name}/permissions",
    response_model=None,
    summary="List permissions on a pipeline",
)
def list_permissions(
    pipeline_name: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List all permission entries for a specific pipeline (owner/admin only)."""
    pipeline_name = _require_owner(db, pipeline_name, current_user)
    permissions = (
        db.query(PipelinePermission)
        .filter(PipelinePermission.pipeline_name == pipeline_name)
        .order_by(PipelinePermission.created_at.desc())
        .all()
    )
    return {
        "pipeline_name": pipeline_name,
        "permissions": [_permission_to_response(p) for p in permissions],
        "total": len(permissions),
    }


@router.delete(
    "/{pipeline_name}/permissions/{user_id}",
    summary="Revoke a permission on a pipeline",
)
def revoke_permission(
    pipeline_name: str,
    user_id: str,
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Revoke a user's permission on a specific pipeline (owner only)."""
    pipeline_name = _require_owner(db, pipeline_name, current_user)
    validate_uuid_format(user_id)

    perm = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == as_uuid(user_id),
        )
        .first()
    )
    if not perm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found")

    # Last-owner guard (M-153): revoking the final OWNER grant would orphan
    # the pipeline with no one able to administer it. Refuse unless another
    # ownership path (a second OWNER grant or a contract owner) remains.
    perm_level = (
        perm.permission_level.value
        if hasattr(perm.permission_level, "value")
        else str(perm.permission_level)
    )
    if perm_level == "owner":
        remaining_owners = (
            db.query(PipelinePermission.id)
            .filter(
                PipelinePermission.pipeline_name == pipeline_name,
                PipelinePermission.permission_level == PermissionLevel.OWNER,
                PipelinePermission.user_id != as_uuid(user_id),
            )
            .first()
        )
        contract_owner = (
            db.query(PipelineContract.id)
            .filter(PipelineContract.pipeline_name == pipeline_name)
            .first()
        )
        if remaining_owners is None and contract_owner is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot revoke the last owner grant on a pipeline; "
                "transfer ownership first.",
            )

    db.delete(perm)
    db.commit()

    log_action(
        db,
        "permission_revoked",
        user_id=current_user.id,
        resource_type="pipeline_permission",
        resource_id=as_uuid(user_id),
        details={"pipeline_name": pipeline_name, "target_user": user_id},
        request=request,
    )

    return {
        "detail": f"Permission revoked for user '{user_id}' on pipeline '{pipeline_name}'"
    }
