"""Webhook CRUD and delivery endpoints."""

import hmac
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User, Webhook, WebhookDelivery
from backend.utils.uuid_utils import as_uuid as _as_uuid
from backend.services.audit_service import log_action
from backend.config import settings
from backend.utils.url_security import (
    UnsafeURL,
    prepare_public_httpx_request,
    validate_public_http_url,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Schemas


class WebhookCreate(BaseModel):
    url: str
    secret: Optional[str] = None
    events: Optional[List[str]] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            return validate_public_http_url(v)
        except UnsafeURL as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("secret")
    @classmethod
    def validate_secret_strength(cls, v: Optional[str]) -> Optional[str]:
        # MED-12: empty secret is permitted (we fall back to the platform
        # WEBHOOK_SIGNING_SECRET), but if a secret is supplied it must be
        # at least 16 chars to resist dictionary attacks.
        if v is None:
            return v
        if len(v) < 16:
            raise ValueError(
                "Webhook secret must be at least 16 characters when supplied"
            )
        return v


class WebhookResponse(BaseModel):
    id: str
    url: str
    events: list
    is_active: bool
    created_at: datetime
    has_secret: bool = False

    model_config = ConfigDict(from_attributes=True)


class DeliveryResponse(BaseModel):
    id: str
    event_type: str
    response_status: Optional[int]
    attempt_number: int
    delivered_at: Optional[datetime]
    failed_at: Optional[datetime]
    error_message: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestWebhookResponse(BaseModel):
    delivered: bool
    response_status: Optional[int] = None
    error: Optional[str] = None

# Endpoints


def _parse_webhook_id(webhook_id: str):
    try:
        return _as_uuid(webhook_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook_id",
        ) from exc


@router.post("/", response_model=WebhookResponse,
             status_code=status.HTTP_201_CREATED)
def create_webhook(
    body: WebhookCreate,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Register a new webhook URL."""
    count = db.query(Webhook).filter(
        Webhook.user_id == current_user.id).count()
    if count >= 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 webhooks per user")

    webhook = Webhook(
        user_id=current_user.id,
        url=body.url,
        secret=body.secret,
        events=body.events or ["pipeline_completed", "pipeline_failed"],
    )
    db.add(webhook)
    db.commit()
    db.refresh(webhook)

    log_action(
        db,
        "webhook_created",
        user_id=current_user.id,
        resource_type="webhook",
        resource_id=webhook.id,
        details={
            "url": body.url})

    return WebhookResponse(
        id=str(webhook.id),
        url=webhook.url,
        events=webhook.events,
        is_active=webhook.is_active,
        created_at=webhook.created_at,
        has_secret=webhook.secret is not None,
    )


@router.get("/", response_model=List[WebhookResponse])
def list_webhooks(
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """List current user's webhooks."""
    webhooks = db.query(Webhook).filter(
        Webhook.user_id == current_user.id).all()
    return [
        WebhookResponse(
            id=str(w.id), url=w.url, events=w.events,
            is_active=w.is_active, created_at=w.created_at,
            has_secret=w.secret is not None,
        )
        for w in webhooks
    ]


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    webhook_id: str,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Delete a webhook (own only)."""
    parsed_webhook_id = _parse_webhook_id(webhook_id)
    webhook = db.query(Webhook).filter(
        Webhook.id == parsed_webhook_id
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")

    log_action(
        db,
        "webhook_deleted",
        user_id=current_user.id,
        resource_type="webhook",
        resource_id=webhook.id)

    db.delete(webhook)
    db.commit()


@router.get("/{webhook_id}/deliveries", response_model=List[DeliveryResponse])
def list_deliveries(
    webhook_id: str,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """List delivery attempts for a webhook."""
    parsed_webhook_id = _parse_webhook_id(webhook_id)
    webhook = db.query(Webhook).filter(
        Webhook.id == parsed_webhook_id
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")

    deliveries = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == parsed_webhook_id)
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
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Send a test payload to a webhook URL."""
    parsed_webhook_id = _parse_webhook_id(webhook_id)
    webhook = db.query(Webhook).filter(
        Webhook.id == parsed_webhook_id
    ).first()
    if not webhook:
        raise HTTPException(status_code=404, detail="Webhook not found")
    if str(webhook.user_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your webhook")
    try:
        validate_public_http_url(webhook.url)
    except UnsafeURL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook URL is not allowed",
        )

    payload = {
        "event": "test",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {"message": "This is a test webhook from PipelineIQ"},
    }
    # MED-13: sort keys for cross-path signature consistency. HIGH-18: include
    # timestamp in HMAC + headers so the test signature is replay-resistant.
    body = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": "test",
        "X-PipelineIQ-Timestamp": ts,
    }
    if webhook.secret:
        sig = hmac.new(
            webhook.secret.encode(),
            ts.encode("ascii") + b"." + body,
            hashlib.sha256).hexdigest()
        headers["X-PipelineIQ-Signature"] = f"sha256={sig}"
        headers["X-PipelineIQ-Signature-Timestamp"] = ts

    try:
        prepared_request = prepare_public_httpx_request(webhook.url)
        headers.update(prepared_request.headers)
        # CRIT-01: never follow redirects from webhook test posts.
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            resp = client.request(
                "POST",
                prepared_request.url,
                content=body,
                headers=headers,
                extensions=prepared_request.extensions,
            )
        return TestWebhookResponse(
            delivered=200 <= resp.status_code < 300,
            response_status=resp.status_code,
        )
    except Exception:
        logger.warning("Webhook test delivery failed", exc_info=True)
        return TestWebhookResponse(delivered=False, error="Failed to connect to webhook URL")
