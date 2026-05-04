"""Audit log API endpoints."""

import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.auth import get_current_user, get_current_admin
from backend.dependencies import get_read_db_dependency
from backend.models import AuditLog, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/audit", tags=["Audit"])


class AuditLogResponse(BaseModel):
    id: str
    user_id: Optional[str]
    action: str
    resource_type: Optional[str]
    resource_id: Optional[str]
    details: Optional[dict]
    ip_address: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("/logs", response_model=List[AuditLogResponse])
def get_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    action: Optional[str] = None,
    user_id: Optional[str] = None,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_admin),
):
    """Get audit logs (admin only)."""
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if user_id:
        import uuid
        query = query.filter(AuditLog.user_id == uuid.UUID(user_id))

    logs = (
        query.order_by(AuditLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return [
        AuditLogResponse(
            id=str(log.id), user_id=str(log.user_id) if log.user_id else None,
            action=log.action, resource_type=log.resource_type,
            resource_id=str(log.resource_id) if log.resource_id else None,
            details=log.details, ip_address=log.ip_address,
            created_at=log.created_at,
        )
        for log in logs
    ]


@router.get("/logs/mine", response_model=List[AuditLogResponse])
def get_my_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Get current user's audit logs."""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.user_id == current_user.id)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return [
        AuditLogResponse(
            id=str(log.id), user_id=str(log.user_id) if log.user_id else None,
            action=log.action, resource_type=log.resource_type,
            resource_id=str(log.resource_id) if log.resource_id else None,
            details=log.details, ip_address=log.ip_address,
            created_at=log.created_at,
        )
        for log in logs
    ]
