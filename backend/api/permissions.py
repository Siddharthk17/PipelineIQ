"""Per-pipeline RBAC API endpoints.

Provides fine-grained permission management for individual pipelines,
allowing owners to grant runner or viewer access to other users.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import PermissionLevel, PipelinePermission, User
from backend.services.audit_service import log_action
from backend.utils.uuid_utils import validate_uuid_format, as_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pipelines", tags=["permissions"])


class GrantPermissionRequest(BaseModel):
    """Request body to grant a permission on a pipeline."""

    user_id: str = Field(..., description="UUID of the user to grant permission to")
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


def _require_owner(db: Session, pipeline_name: str, user: User) -> None:
    """Raise 403 if the user is not an owner of the pipeline."""
    owner_perm = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == user.id,
            PipelinePermission.permission_level == PermissionLevel.OWNER,
        )
        .first()
    )
    # Also allow admins
    if not owner_perm and user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only pipeline owners or admins can manage permissions",
        )


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
    _require_owner(db, pipeline_name, current_user)

    try:
        level = PermissionLevel(body.permission_level)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid permission level '{body.permission_level}'. Must be 'owner', 'runner', or 'viewer'.",
        )

    validate_uuid_format(body.user_id)
    target_user = db.query(User).filter(User.id == as_uuid(body.user_id)).first()
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Target user not found"
        )

    # Check if permission already exists
    existing = (
        db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == as_uuid(body.user_id),
        )
        .first()
    )
    if existing:
        # Update existing permission
        existing.permission_level = level
        db.commit()
        db.refresh(existing)
        return _permission_to_response(existing)

    perm = PipelinePermission(
        pipeline_name=pipeline_name,
        user_id=as_uuid(body.user_id),
        permission_level=level,
    )
    db.add(perm)
    db.commit()
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
    _require_owner(db, pipeline_name, current_user)
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
    _require_owner(db, pipeline_name, current_user)
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
            status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found"
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
