"""Webhook delivery service with HMAC signing and async Celery delivery.

Delegates to Celery tasks for non-blocking HTTP delivery via
httpx.AsyncClient. The webhook_deliveries table records every attempt.
"""

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import orjson

from backend.database import SessionLocal
from backend.models import Webhook, WebhookDelivery
from backend.config import settings
from backend.utils.url_security import UnsafeURL, prepare_public_httpx_request

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = (0, 30, 120)


def _sign_payload(secret: str, body: str | bytes, timestamp: str | None = None) -> str:
    """HMAC-SHA256 over `<timestamp>.<body>`.

    MED-13: body is canonicalized with sorted keys before signing so the
    sync and async paths produce identical signatures.
    HIGH-18: callers should pass `timestamp` and include it in the response
    headers so recipients can reject replays outside a 5-minute window.
    """
    body_bytes = body if isinstance(body, bytes) else body.encode()
    ts = timestamp or str(int(time.time()))
    return hmac.new(
        secret.encode(),
        ts.encode("ascii") + b"." + body_bytes,
        hashlib.sha256,
    ).hexdigest()


def deliver_webhook_sync(
    webhook: Webhook,
    event_type: str,
    payload: dict,
    db=None,
) -> WebhookDelivery:
    """Synchronous webhook delivery — used for test webhooks only."""
    own_session = db is None
    if own_session:
        db = SessionLocal()

    # MED-13: sort keys in both async and sync paths so signatures match.
    body = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    # HIGH-18: timestamp included in HMAC to defeat replay.
    ts = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": event_type,
        "X-PipelineIQ-Timestamp": ts,
    }
    if webhook.secret:
        sig = _sign_payload(webhook.secret, body, timestamp=ts)
        headers["X-PipelineIQ-Signature"] = f"sha256={sig}"
        headers["X-PipelineIQ-Signature-Timestamp"] = ts

    delivery = WebhookDelivery(
        webhook_id=webhook.id,
        run_id=payload.get("data", {}).get("run_id"),
        event_type=event_type,
        payload=payload,
        attempt_number=1,
    )

    try:
        prepared_request = prepare_public_httpx_request(webhook.url)
        import httpx

        # CRIT-01: do not follow redirects so a webhook cannot pivot the
        # request to internal targets.
        headers.update(prepared_request.headers)
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            resp = client.request(
                "POST",
                prepared_request.url,
                content=body,
                headers=headers,
                extensions=prepared_request.extensions,
            )
        delivery.response_status = resp.status_code
        delivery.response_body = resp.text[:1000] if resp.text else None
        if 200 <= resp.status_code < 300:
            delivery.delivered_at = datetime.now(timezone.utc)
        else:
            delivery.failed_at = datetime.now(timezone.utc)
            delivery.error_message = f"HTTP {resp.status_code}"
    except UnsafeURL:
        delivery.failed_at = datetime.now(timezone.utc)
        delivery.error_message = "Webhook URL is not allowed"
    except Exception as e:
        delivery.failed_at = datetime.now(timezone.utc)
        delivery.error_message = str(e)[:500]

    db.add(delivery)
    db.commit()
    if own_session:
        db.close()
    return delivery


def trigger_webhooks_for_run(
    run_id: str,
    status: str,
    pipeline_name: str = "",
    duration_ms: int = 0,
    steps_count: int = 0,
    rows_processed: int = 0,
    user_id: str = "",
) -> dict[str, Any]:
    """Fire webhooks via Celery tasks for non-blocking delivery."""
    event_type = f"pipeline_{status.lower()}"

    if not user_id:
        return {
            "status": "skipped",
            "matched_webhooks": 0,
            "delivered": 0,
            "failed": 0,
        }

    try:
        from backend.tasks.webhook_tasks import fire_webhooks_for_event

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
        fire_webhooks_for_event(event_type, payload, user_id)

        return {
            "status": "enqueued",
            "matched_webhooks": -1,
            "delivered": 0,
            "failed": 0,
        }
    except Exception as e:
        logger.error("Webhook trigger failed: %s", e)
        return {"status": "failed", "matched_webhooks": 0, "delivered": 0, "failed": 0}


def deliver_webhook(
    webhook: Webhook,
    event_type: str,
    payload: dict,
    db=None,
) -> WebhookDelivery:
    """Convenience wrapper for test webhooks — uses sync delivery."""
    return deliver_webhook_sync(webhook, event_type, payload, db=db)


def deliver_with_retry(
    webhook_id: UUID,
    event_type: str,
    payload: dict,
) -> None:
    """Enqueue webhook delivery as a Celery task.

    The deliver_webhook task handles retries with exponential backoff
    and records delivery attempts in webhook_deliveries.
    """
    from backend.tasks.webhook_tasks import deliver_webhook as deliver_task

    payload_with_event = {**payload, "event_type": event_type}
    deliver_task.apply_async(
        args=[str(webhook_id), payload_with_event],
        queue="critical",
    )
