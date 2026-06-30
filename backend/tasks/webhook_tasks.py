"""Celery task for asynchronous webhook delivery.

Uses httpx.AsyncClient with connection pooling to avoid blocking the
event loop. Delivery is fully async — the Celery worker thread creates
a temporary event loop to execute the async HTTP call.

WebhookTask base class provides a shared httpx.AsyncClient per worker
process. Connection pooling means multiple webhooks share TCP connections
to the same host.
"""

import asyncio
import hashlib
import hmac
import orjson
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import httpx
from celery import Task

from backend.celery_app import celery_app
from backend.config import settings
from backend.utils.url_security import UnsafeURL, prepare_public_httpx_request

logger = logging.getLogger(__name__)

# HIGH-18: webhook signatures include a Unix timestamp; recipients must reject
# deliveries outside this window. Combined with HMAC, this defeats replay.
WEBHOOK_REPLAY_WINDOW_SECONDS = 300


class WebhookTask(Task):
    _client: httpx.AsyncClient | None = None
    # LOW-20: max retention for delivery records (days). Older rows are
    # pruned by the storage-maintenance beat task to prevent unbounded
    # database growth from high-volume webhook traffic.
    DELIVERY_RETENTION_DAYS = 30

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=10.0,
                    write=5.0,
                    pool=2.0,
                ),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20,
                ),
                # CRIT-01: do NOT follow redirects. validate_public_http_url
                # pins the IP at config time; following redirects would let
                # a server pivot the request to internal/private targets
                # (SSRF). Outbound clients resolve to the pinned IP only.
                follow_redirects=False,
                trust_env=False,
            )
        return self._client


@celery_app.task(
    name="tasks.deliver_webhook",
    base=WebhookTask,
    queue="critical",
    bind=True,
    max_retries=3,
    autoretry_for=(httpx.RequestError, httpx.TimeoutException),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def deliver_webhook(self, webhook_id: str, payload: dict) -> dict:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_deliver_async(self, webhook_id, payload))
    finally:
        loop.close()


async def _deliver_async(task: WebhookTask, webhook_id: str, payload: dict) -> dict:
    from backend.database import SessionLocal
    from backend.models import Webhook, WebhookDelivery

    db = SessionLocal()
    try:
        webhook = db.query(Webhook).filter(Webhook.id == webhook_id).first()
    finally:
        db.close()

    if not webhook:
        logger.error("Webhook not found: %s", webhook_id)
        return {"success": False, "error": "Webhook not found"}

    if not webhook.is_active:
        logger.debug("Webhook %s is inactive — skipping delivery", webhook_id)
        return {"success": True, "skipped": True}

    try:
        # CRIT-01: validate/re-resolve at delivery time. HTTP destinations
        # are rewritten to the pinned IP with the original Host header;
        # redirects remain disabled on the client to stop private pivots.
        prepared_request = prepare_public_httpx_request(webhook.url)
    except UnsafeURL:
        logger.warning("Webhook delivery blocked for unsafe URL: id=%s", webhook_id)
        return {"success": False, "error": "Webhook URL is not allowed"}

    # MED-13: always sign a sorted-keys canonical body for cross-implementation parity.
    payload_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    # HIGH-18: include an explicit Unix timestamp in the signed material so
    # recipients can reject replayed deliveries older than the replay window.
    ts = str(int(time.time()))
    secret_bytes = (
        webhook.secret.encode("utf-8")
        if webhook.secret
        else settings.WEBHOOK_SIGNING_SECRET.encode("utf-8")
    )
    signature = hmac.new(
        secret_bytes,
        ts.encode("ascii") + b"." + payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    attempt_number = (task.request.retries or 0) + 1
    start_ms = int(time.time() * 1000)

    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": payload.get("event_type", "unknown"),
        "X-PipelineIQ-Delivery-ID": str(task.request.id),
        "X-PipelineIQ-Timestamp": ts,
        "User-Agent": "PipelineIQ/2.12.0",
    }
    if webhook.secret or settings.WEBHOOK_SIGNING_SECRET:
        headers["X-PipelineIQ-Signature"] = f"sha256={signature}"
        headers["X-PipelineIQ-Signature-Timestamp"] = ts
    headers.update(prepared_request.headers)

    try:
        resp = await task.client.request(
            "POST",
            prepared_request.url,
            content=payload_bytes,
            headers=headers,
            extensions=prepared_request.extensions,
        )
        duration_ms = int(time.time() * 1000) - start_ms
        success = 200 <= resp.status_code < 300

        logger.info(
            "Webhook delivered: url=%s, status=%d, duration=%dms",
            webhook.url[:60],
            resp.status_code,
            duration_ms,
        )

        db = SessionLocal()
        try:
            delivery = WebhookDelivery(
                webhook_id=webhook_id,
                run_id=payload.get("data", {}).get("run_id"),
                event_type=payload.get("event_type", "unknown"),
                payload=payload,
                response_status=resp.status_code,
                response_body=resp.text[:1000] if resp.text else None,
                attempt_number=attempt_number,
                duration_ms=duration_ms,
            )
            if success:
                delivery.delivered_at = datetime.now(timezone.utc)
            else:
                delivery.failed_at = datetime.now(timezone.utc)
                delivery.error_message = f"HTTP {resp.status_code}"
            db.add(delivery)
            db.commit()
        finally:
            db.close()

        if not success:
            raise httpx.RequestError(
                f"Webhook returned {resp.status_code}: {resp.text[:200]}"
            )

        return {"success": True, "status_code": resp.status_code, "duration_ms": duration_ms}

    except (httpx.RequestError, httpx.TimeoutException) as e:
        duration_ms = int(time.time() * 1000) - start_ms
        error_str = str(e)[:500]

        db = SessionLocal()
        try:
            delivery = WebhookDelivery(
                webhook_id=webhook_id,
                run_id=payload.get("data", {}).get("run_id"),
                event_type=payload.get("event_type", "unknown"),
                payload=payload,
                attempt_number=attempt_number,
                duration_ms=duration_ms,
            )
            delivery.failed_at = datetime.now(timezone.utc)
            delivery.error_message = error_str
            db.add(delivery)
            db.commit()
        finally:
            db.close()

        logger.warning(
            "Webhook delivery failed (attempt %d): url=%s, error=%s",
            attempt_number,
            webhook.url[:60],
            error_str,
        )
        raise


@celery_app.task(
    bind=True,
    name="webhooks.deliver",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def deliver_webhooks_task(
    self,
    run_id: str,
    status: str,
    pipeline_name: str = "",
    duration_ms: int = 0,
    steps_count: int = 0,
    rows_processed: int = 0,
    user_id: str = "",
) -> Dict[str, Any]:
    """Deliver webhooks for a pipeline run asynchronously.

    Enqueues individual deliver_webhook tasks for each active webhook
    subscribed to this event type. Returns immediately — the actual
    HTTP calls happen in separate worker tasks.
    """
    try:
        from backend.database import SessionLocal
        from backend.models import Webhook

        db = SessionLocal()
        try:
            query = db.query(Webhook).filter(Webhook.is_active)
            if user_id:
                query = query.filter(Webhook.user_id == user_id)
            webhooks = query.all()
        finally:
            db.close()

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
            "event_type": event_type,
        }

        matched = 0
        for wh in webhooks:
            if event_type in (wh.events or []):
                matched += 1
                deliver_webhook.apply_async(
                    args=[str(wh.id), payload],
                    queue="critical",
                )

        return {
            "run_id": run_id,
            "status": "enqueued" if matched > 0 else "skipped",
            "matched_webhooks": matched,
        }
    except Exception as exc:
        logger.error("Webhook delivery failed for run %s: %s", run_id, exc)
        raise self.retry(exc=exc)


def fire_webhooks_for_event(
    event_type: str, payload: dict, user_id: str
) -> None:
    """Fire webhooks for a specific event type for a given user.

    Non-blocking: enqueues Celery tasks without waiting for delivery.
    Returns immediately if user_id is empty (no webhooks to fire).
    """
    if not user_id:
        return

    from backend.database import SessionLocal
    from backend.models import Webhook

    db = SessionLocal()
    try:
        webhooks = (
            db.query(Webhook)
            .filter(Webhook.user_id == user_id, Webhook.is_active.is_(True))
            .all()
        )
    finally:
        db.close()

    payload_with_event = {**payload, "event_type": event_type}

    for wh in webhooks:
        if event_type in (wh.events or []):
            deliver_webhook.apply_async(
                args=[str(wh.id), payload_with_event],
                queue="critical",
            )
            logger.debug(
                "Enqueued webhook delivery: %s for event %s",
                wh.url[:60],
                event_type,
            )
