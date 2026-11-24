"""Webhook CRUD and delivery endpoints."""

import logging
from datetime import UTC, datetime

import httpx
import orjson
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User, Webhook, WebhookDelivery
from backend.services.audit_service import log_action
from backend.utils.url_security import (
    UnsafeURL,
    build_webhook_signature,
    pin_url_to_validated_ip,
    validate_public_http_url,
)
from backend.utils.uuid_utils import as_uuid as _as_uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Schemas


class WebhookCreate(BaseModel):
    url: str
    secret: str | None = None
    events: list[str] | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        try:
            return validate_public_http_url(v)
        except UnsafeURL as exc:
            raise ValueError(str(exc)) from exc


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
    response_status: int | None
    attempt_number: int
    delivered_at: datetime | None
    failed_at: datetime | None
    error_message: str | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TestWebhookResponse(BaseModel):
    delivered: bool
    response_status: int | None = None
    error: str | None = None

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
    request: Request,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
):
    """Register a new webhook URL."""
    # Serialize creation per user so the cap below is atomic under
    # concurrency: without the row lock, two concurrent requests can both
    # observe count == 9 and both insert, overshooting the limit (M-155).
    # The lock is held until commit; on SQLite it is a no-op.
    db.query(User).filter(User.id == current_user.id).with_for_update().first()
    # Archived webhooks (soft-deleted, M-026) no longer fire, so only
    # active ones consume cap budget.
    count = db.query(Webhook).filter(
        Webhook.user_id == current_user.id,
        Webhook.is_active.is_(True)).count()
    if count >= 10:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 webhooks per user")

    # R2-004: honor Idempotency-Key so a retried create does not
    # double-register (double fan-out deliveries). Claimed after the cap
    # check so validation failures never hold a placeholder. Mirrors M-062.
    from backend.api.contracts import (
        _get_idempotency_key,
        _idempotency_claim,
        _idempotency_complete,
        _idempotency_release,
        _idempotency_wait,
    )

    idem_key = _get_idempotency_key(request)
    idem_row = None
    if idem_key is not None:
        action, idem_row = _idempotency_claim(
            db, scope="webhook_create", key=idem_key,
            user_id=current_user.id,
        )
        if action == "replay":
            assert idem_row is not None and idem_row.response_body is not None
            return WebhookResponse(**idem_row.response_body)
        if action == "inflight":
            assert idem_row is not None
            done = _idempotency_wait(db, idem_row)
            if done is not None and done.response_body is not None:
                return WebhookResponse(**done.response_body)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate idempotent request still in progress",
            )

    try:
        webhook = Webhook(
            user_id=current_user.id,
            url=body.url,
            secret=body.secret,
            events=body.events or ["pipeline_completed", "pipeline_failed"],
        )
        db.add(webhook)
        try:
            db.commit()
        except Exception:
            # Leave the session usable for the caller (M-173); the dependency
            # closes it, but a poisoned session would break retries/audit.
            db.rollback()
            raise
        db.refresh(webhook)

        log_action(
            db,
            "webhook_created",
            user_id=current_user.id,
            resource_type="webhook",
            resource_id=webhook.id,
            details={
                "url": body.url})

        response = WebhookResponse(
            id=str(webhook.id),
            url=webhook.url,
            events=webhook.events,
            is_active=webhook.is_active,
            created_at=webhook.created_at,
            has_secret=webhook.secret is not None,
        )
        if idem_row is not None:
            _idempotency_complete(
                db,
                idem_row,
                status_code=status.HTTP_201_CREATED,
                body=response.model_dump(mode="json"),
                resource_id=str(webhook.id),
            )
    except Exception:
        # Never pin the key on a failure (M-062): without this, one DB
        # blip after the claim would 409 every future retry forever.
        if idem_row is not None:
            _idempotency_release(db, idem_row)
        raise
    else:
        return response


@router.get("/", response_model=list[WebhookResponse])
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
        # R2-006: 404 (not 403) so webhook IDs cannot be probed.
        raise HTTPException(status_code=404, detail="Webhook not found")

    # Audit AFTER commit: log_action writes through its own session, so
    # logging before the delete commits would record deletions that later
    # roll back. Capture the id first — the instance is expired post-commit.
    resource_id = webhook.id
    has_history = (
        db.query(WebhookDelivery)
        .filter(WebhookDelivery.webhook_id == parsed_webhook_id)
        .limit(1)
        .first()
        is not None
    )
    try:
        if has_history:
            # Append-only delivery history (M-026, M-081): a Postgres RULE
            # replaces DELETEs on webhook_deliveries with NOTHING, so a
            # cascaded parent delete would die on the FK (500 on PG);
            # deleting the rows first would silently destroy the audit
            # trail. Archive the webhook instead: deliveries stay
            # queryable and the webhook stops firing (fan-out filters on
            # is_active).
            webhook.is_active = False
        else:
            db.delete(webhook)
        db.commit()
    except Exception:
        # Session cleanup for commit failures (M-173).
        db.rollback()
        raise

    log_action(
        db,
        "webhook_deleted",
        user_id=current_user.id,
        resource_type="webhook",
        resource_id=resource_id)


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryResponse])
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
        # R2-006: 404 (not 403) so webhook IDs cannot be probed.
        raise HTTPException(status_code=404, detail="Webhook not found")

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
        # R2-006: 404 (not 403) so webhook IDs cannot be probed.
        raise HTTPException(status_code=404, detail="Webhook not found")
    try:
        validate_public_http_url(webhook.url)
        pinned_url, host_header, extensions = pin_url_to_validated_ip(
            webhook.url)
    except UnsafeURL as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook URL is not allowed",
        ) from exc

    payload = {
        "event": "test",
        "timestamp": datetime.now(UTC).isoformat(),
        "data": {"message": "This is a test webhook from PipelineIQ"},
    }
    # Identical serialization and HMAC construction as the async delivery
    # task (see build_webhook_signature): the receiver must verify the
    # same bytes the worker would send.
    body = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": "test",
        "Host": host_header,
    }
    if webhook.secret:
        sig = build_webhook_signature(webhook.secret, body)
        headers["X-PipelineIQ-Signature"] = f"sha256={sig}"

    try:
        with httpx.Client(timeout=10) as client:
            # Pinned target: connect to a validated IP, not to whatever a
            # later DNS lookup returns (DNS-rebinding TOCTOU). SNI keeps
            # HTTPS certificate verification against the original host.
            resp = client.post(
                pinned_url, content=body, headers=headers,
                extensions=extensions,
            )
        return TestWebhookResponse(
            delivered=200 <= resp.status_code < 300,
            response_status=resp.status_code,
        )
    except Exception:
        logger.warning("Webhook test delivery failed", exc_info=True)
        return TestWebhookResponse(delivered=False, error="Failed to connect to webhook URL")
