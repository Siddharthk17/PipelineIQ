"""Column-level access policy CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.database import get_read_db, get_write_db
from backend.models import FileProfile, UploadedFile, User
from backend.models.column_policy import ColumnPolicy
from backend.security.column_security import detect_pii_columns, invalidate_policy_cache
from backend.utils.uuid_utils import as_uuid, validate_uuid_format

router = APIRouter(prefix="/api/column-policies", tags=["Column Security"])


class CreateColumnPolicyRequest(BaseModel):
    file_id: str = Field(..., min_length=1)
    column_name: str = Field(..., min_length=1, max_length=500)
    policy: str = Field(..., pattern="^(redacted|masked)$")
    mask_pattern: str | None = None
    allowed_roles: list[str] = Field(default_factory=list)


@router.post("", status_code=201)
def create_column_policy(
    request: CreateColumnPolicyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db),
):
    validate_uuid_format(request.file_id)
    file_record = (
        db.query(UploadedFile)
        .filter(UploadedFile.id == as_uuid(request.file_id))
        .first()
    )
    if not file_record:
        raise HTTPException(404, "File not found")
    if current_user.role != "admin" and str(file_record.user_id) != str(current_user.id):
        raise HTTPException(403, "Not authorized for this file")

    existing = (
        db.query(ColumnPolicy)
        .filter(
            ColumnPolicy.file_id == request.file_id,
            ColumnPolicy.column_name == request.column_name,
        )
        .first()
    )

    if existing:
        existing.policy = request.policy
        existing.mask_pattern = request.mask_pattern
        existing.allowed_roles = request.allowed_roles
    else:
        existing = ColumnPolicy(
            file_id=request.file_id,
            column_name=request.column_name,
            policy=request.policy,
            mask_pattern=request.mask_pattern,
            allowed_roles=request.allowed_roles,
            created_by=current_user.id,
        )
        db.add(existing)

    db.commit()
    invalidate_policy_cache(request.file_id)

    return {"id": str(existing.id), "message": "Policy created/updated"}


@router.get("/files/{file_id}")
def list_file_policies(
    file_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db),
):
    validate_uuid_format(file_id)
    file_record = db.query(UploadedFile).filter(UploadedFile.id == as_uuid(file_id)).first()
    if not file_record:
        raise HTTPException(404, "File not found")
    if current_user.role != "admin" and str(file_record.user_id) != str(current_user.id):
        raise HTTPException(403, "Not authorized for this file")

    policies = (
        db.query(ColumnPolicy).filter(ColumnPolicy.file_id == file_id).all()
    )

    profile_record = (
        db.query(FileProfile).filter(FileProfile.file_id == file_id).first()
    )
    pii_suggestions = []
    if profile_record and profile_record.profile:
        pii_suggestions = detect_pii_columns(profile_record.profile)

    existing_cols = {p.column_name for p in policies}
    pii_suggestions = [c for c in pii_suggestions if c not in existing_cols]

    return {
        "file_id": file_id,
        "policies": [
            {
                "id": str(p.id),
                "column_name": p.column_name,
                "policy": p.policy,
                "mask_pattern": p.mask_pattern,
                "allowed_roles": list(p.allowed_roles or []),
                "created_at": p.created_at.isoformat(),
            }
            for p in policies
        ],
        "pii_suggestions": pii_suggestions,
    }


@router.delete("/{policy_id}", status_code=204)
def delete_column_policy(
    policy_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_write_db),
):
    policy = (
        db.query(ColumnPolicy).filter(ColumnPolicy.id == policy_id).first()
    )
    if not policy:
        raise HTTPException(404, "Policy not found")
    file_record = db.query(UploadedFile).filter(UploadedFile.id == policy.file_id).first()
    if not file_record:
        raise HTTPException(404, "File not found")
    if current_user.role != "admin" and str(file_record.user_id) != str(current_user.id):
        raise HTTPException(403, "Not authorized for this file")

    file_id = str(policy.file_id)
    db.delete(policy)
    db.commit()
    invalidate_policy_cache(file_id)
