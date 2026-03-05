"""Webhook CRUD and delivery endpoints."""

import hmac
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_db_dependency
from backend.models import User, Webhook, WebhookDelivery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


# ── Schemas ─────────────────────────────────────────────────────────

class WebhookCreate(BaseModel):
    url: str
    secret: Optional[str] = None
    events: Optional[List[str]] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: list
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DeliveryResponse(BaseModel):
    id: str
    event_type: str
    response_status: Optional[int]
    attempt_number: int
    delivered_at: Optional[datetime]
    failed_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class TestWebhookResponse(BaseModel):
    delivered: bool
    response_status: Optional[int] = None
    error: Optional[str] = None


def _as_uuid(val):
    import uuid as _uuid
    if isinstance(val, _uuid.UUID):
        return val
    return _uuid.UUID(str(val))


# ── Endpoints ───────────────────────────────────────────────────────

@router.post("/", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
def create_webhook(
    body: WebhookCreate,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Register a new webhook URL."""
    count = db.query(Webhook).filter(Webhook.user_id == current_user.id).count()
    if count >= 10:
        raise HTTPException(status_code=400, detail="Maximum 10 webhooks per user")

    webhook = Webhook(
        user_id=current_user.id,
        url=body.url,
        secret=body.secret,
        events=body.events or ["pipeline_completed", "pipeline_failed"],
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    from backend.services.audit_service import log_action
    log_action(db, "webhook_created", user_id=current_user.id, resource_type="webhook",
               resource_id=webhook.id, details={"url": body.url})

    return WebhookResponse(
        id=str(webhook.id),
        url=webhook.url,
        events=webhook.events,
        is_active=webhook.is_active,
        created_at=webhook.created_at,
    )


@router.get("/", response_model=List[WebhookResponse])
def list_webhooks(
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """List current user's webhooks."""
    webhooks = db.query(Webhook).filter(Webhook.user_id == current_user.id).all()
    return [
        WebhookResponse(
            id=str(w.id), url=w.url, events=w.events,
            is_active=w.is_active, created_at=w.created_at,
        )
        for w in webhooks
    ]


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    webhook_id: str,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Delete a webhook (own only)."""
    webhook = db.query(Webhook).filter(
        Webhook.id == _as_uuid(webhook_id)
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")

    from backend.services.audit_service import log_action
    log_action(db, "webhook_deleted", user_id=current_user.id, resource_type="webhook",
               resource_id=webhook.id)

    db.delete(webhook)
    db.commit()


@router.get("/{webhook_id}/deliveries", response_model=List[DeliveryResponse])
def list_deliveries(
    webhook_id: str,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """List delivery attempts for a webhook."""
    webhook = db.query(Webhook).filter(
        Webhook.id == _as_uuid(webhook_id)
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")

    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == _as_uuid(webhook_id))
        .order_by(WebhookDelivery.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        DeliveryResponse(
            id=str(d.id), event_type=d.event_type,
            response_status=d.response_status,
            attempt_number=d.attempt_number,
            delivered_at=d.delivered_at, failed_at=d.failed_at,
            error_message=d.error_message, created_at=d.created_at,
        )
        for d in deliveries
    ]


@router.post("/{webhook_id}/test", response_model=TestWebhookResponse)
def test_webhook(
    webhook_id: str,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Send a test payload to a webhook URL."""
    webhook = db.query(Webhook).filter(
        Webhook.id == _as_uuid(webhook_id)
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")

    payload = {
        "event": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"message": "This is a test webhook from PipelineIQ"},
    }
    body = json.dumps(payload)
    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": "test",
    }
    if webhook.secret:
        sig = hmac.new(webhook.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-PipelineIQ-Signature"] = f"sha256={sig}"

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook.url, content=body, headers=headers)
        return TestWebhookResponse(
            delivered=200 <= resp.status_code < 300,
            response_status=resp.status_code,
        )
    except Exception as e:
        return TestWebhookResponse(delivered=False, error=str(e))
