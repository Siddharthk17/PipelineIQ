"""Notification configuration API endpoints.

Provides CRUD for user notification channels (Slack, email) and
a test endpoint to verify webhook connectivity.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_db_dependency
from backend.models import NotificationConfig, NotificationType, User
from backend.services.audit_service import log_action
from backend.services.notification_service import send_slack_notification
from backend.utils.uuid_utils import validate_uuid_format, as_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


class CreateNotificationConfigRequest(BaseModel):
    """Request body to create a notification config."""

    type: str = Field(..., description="Notification type: 'slack' or 'email'")
    config: dict = Field(..., description="Channel-specific config (e.g. {'slack_webhook_url': '...'})")
    events: list[str] = Field(
        default=["pipeline_completed", "pipeline_failed"],
        description="Events to subscribe to",
    )


class NotificationConfigResponse(BaseModel):
    """Response for a notification config."""

    id: str
    type: str
    config: dict
    events: list[str]
    is_active: bool
    created_at: str | None = None


def _config_to_response(config: NotificationConfig) -> NotificationConfigResponse:
    return NotificationConfigResponse(
        id=str(config.id),
        type=config.type.value if hasattr(config.type, "value") else str(config.type),
        config=config.config or {},
        events=config.events or [],
        is_active=config.is_active,
        created_at=config.created_at.isoformat() if config.created_at else None,
    )


@router.post(
    "/",
    response_model=NotificationConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification config",
)
def create_notification_config(
    request: Request,
    body: CreateNotificationConfigRequest,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> NotificationConfigResponse:
    """Create a new notification channel configuration."""
    try:
        notif_type = NotificationType(body.type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid notification type '{body.type}'. Must be 'slack' or 'email'.",
        )

    if notif_type == NotificationType.SLACK:
        if not body.config.get("slack_webhook_url"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack config requires 'slack_webhook_url'.",
            )

    config = NotificationConfig(
        user_id=current_user.id,
        type=notif_type,
        config=body.config,
        events=body.events,
        is_active=True,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    log_action(db, "notification_config_created", user_id=current_user.id,
               resource_type="notification_config", resource_id=config.id,
               details={"type": body.type}, request=request)

    return _config_to_response(config)


@router.get(
    "/",
    summary="List notification configs",
)
def list_notification_configs(
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List all notification configs for the authenticated user."""
    configs = (
        db.query(NotificationConfig)
        .filter(NotificationConfig.user_id == current_user.id)
        .order_by(NotificationConfig.created_at.desc())
        .all()
    )
    return {
        "configs": [_config_to_response(c) for c in configs],
        "total": len(configs),
    }


@router.delete(
    "/{config_id}",
    summary="Delete a notification config",
)
def delete_notification_config(
    config_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete a notification config owned by the current user."""
    validate_uuid_format(config_id)
    config = (
        db.query(NotificationConfig)
        .filter(
            NotificationConfig.id == as_uuid(config_id),
            NotificationConfig.user_id == current_user.id,
        )
        .first()
    )
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification config not found")

    db.delete(config)
    db.commit()

    log_action(db, "notification_config_deleted", user_id=current_user.id,
               resource_type="notification_config", resource_id=as_uuid(config_id),
               request=request)

    return {"detail": f"Notification config '{config_id}' deleted"}


@router.post(
    "/{config_id}/test",
    summary="Send a test notification",
)
def test_notification(
    config_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Send a test notification using the specified config."""
    validate_uuid_format(config_id)
    config = (
        db.query(NotificationConfig)
        .filter(
            NotificationConfig.id == as_uuid(config_id),
            NotificationConfig.user_id == current_user.id,
        )
        .first()
    )
    if not config:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification config not found")

    if config.type == NotificationType.SLACK:
        webhook_url = (config.config or {}).get("slack_webhook_url")
        if not webhook_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No slack_webhook_url configured",
            )
        success = send_slack_notification(
            webhook_url,
            "🔔 *PipelineIQ Test Notification*\nThis is a test message to verify your Slack integration.",
        )
        if success:
            return {"detail": "Test notification sent successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to send test notification to Slack",
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Test not implemented for notification type '{config.type}'",
    )
