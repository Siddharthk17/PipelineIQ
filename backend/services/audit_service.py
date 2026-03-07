"""Audit logging service — records all user actions."""

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from backend.models import AuditLog

logger = logging.getLogger(__name__)


def log_action(
    db: Session,
    action: str,
    user_id: Optional[UUID] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[UUID] = None,
    details: Optional[dict] = None,
    request: Optional[Request] = None,
) -> None:
    """Record an audit log entry."""
    ip = None
    ua = None
    if request:
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")

    entry = AuditLog(
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details or {},
        ip_address=ip,
        user_agent=ua,
    )
    db.add(entry)
    try:
        db.commit()
    except Exception as exc:
        logger.error("Failed to write audit log: %s", exc)
        db.rollback()
