"""Webhook delivery service with HMAC signing and retry logic."""

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx

from backend.database import SessionLocal
from backend.models import Webhook, WebhookDelivery

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = [0, 60, 300]  # seconds


def _sign_payload(secret: str, body: str) -> str:
    """Generate HMAC SHA256 signature."""
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


def deliver_webhook(
    webhook: Webhook,
    event_type: str,
    payload: dict,
    db=None,
) -> WebhookDelivery:
    """Attempt to POST payload to webhook URL and record the result."""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    body = json.dumps(payload)
    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": event_type,
    }
    if webhook.secret:
        sig = _sign_payload(webhook.secret, body)
        headers["X-PipelineIQ-Signature"] = f"sha256={sig}"

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        run_id=payload.get("data", {}).get("run_id"),
        event_type=event_type,
        payload=payload,
        attempt_number=1,
    )

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook.url, content=body, headers=headers)
        delivery.response_status = resp.status_code
        delivery.response_body = resp.text[:1000] if resp.text else None
        if 200 <= resp.status_code < 300:
            delivery.delivered_at = datetime.now(timezone.utc)
        else:
            delivery.failed_at = datetime.now(timezone.utc)
            delivery.error_message = f"HTTP {resp.status_code}"
    except Exception as e:
        delivery.failed_at = datetime.now(timezone.utc)
        delivery.error_message = str(e)[:500]

    db.add(delivery)
    db.commit()
    if own_session:
        db.close()
    return delivery


def deliver_with_retry(
    webhook_id: UUID,
    event_type: str,
    payload: dict,
) -> None:
    """Deliver webhook with up to 3 retry attempts."""
    import time

    db = SessionLocal()
    try:
        webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
        if not webhook or not webhook.is_active:
            return

        body = json.dumps(payload)
        headers = {
            "Content-Type": "application/json",
            "X-PipelineIQ-Event": event_type,
        }
        if webhook.secret:
            sig = _sign_payload(webhook.secret, body)
            headers["X-PipelineIQ-Signature"] = f"sha256={sig}"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(RETRY_DELAYS[attempt - 1])

            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                run_id=payload.get("data", {}).get("run_id"),
                event_type=event_type,
                payload=payload,
                attempt_number=attempt,
            )

            try:
                with httpx.Client(timeout=10) as client:
                    resp = client.post(webhook.url, content=body, headers=headers)
                delivery.response_status = resp.status_code
                delivery.response_body = resp.text[:1000] if resp.text else None
                if 200 <= resp.status_code < 300:
                    delivery.delivered_at = datetime.now(timezone.utc)
                    db.add(delivery)
                    db.commit()
                    logger.info("Webhook %s delivered on attempt %d", webhook_id, attempt)
                    return
                else:
                    delivery.failed_at = datetime.now(timezone.utc)
                    delivery.error_message = f"HTTP {resp.status_code}"
            except Exception as e:
                delivery.failed_at = datetime.now(timezone.utc)
                delivery.error_message = str(e)[:500]

            db.add(delivery)
            db.commit()
            logger.warning(
                "Webhook %s attempt %d/%d failed: %s",
                webhook_id, attempt, MAX_ATTEMPTS, delivery.error_message,
            )

        logger.error("Webhook %s permanently failed after %d attempts", webhook_id, MAX_ATTEMPTS)
    finally:
        db.close()


def trigger_webhooks_for_run(run_id: str, status: str, pipeline_name: str = "",
                              duration_ms: int = 0, steps_count: int = 0,
                              rows_processed: int = 0) -> None:
    """Fire webhooks for all users that registered for this event type."""
    event_type = f"pipeline_{status.lower()}"
    payload = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "run_id": str(run_id),
            "pipeline_name": pipeline_name,
            "status": status,
            "duration_ms": duration_ms,
            "steps_count": steps_count,
            "rows_processed": rows_processed,
        },
    }

    db = SessionLocal()
    try:
        webhooks = db.query(Webhook).filter(Webhook.is_active == True).all()
        for wh in webhooks:
            if event_type in (wh.events or []):
                try:
                    deliver_webhook(wh, event_type, payload, db=db)
                except Exception as e:
                    logger.error("Failed to deliver webhook %s: %s", wh.id, e)
    finally:
        db.close()
