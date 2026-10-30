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
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
from celery import Task

from backend.celery_app import celery_app
from backend.utils.url_security import UnsafeURL, validate_public_http_url

logger = logging.getLogger(__name__)


class WebhookTask(Task):
    _client: httpx.AsyncClient | None = None

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
                # Redirects are followed manually by _post_with_safe_redirects
                # so every hop can be re-validated. Never enable httpx-level
                # redirects here: they would skip the egress allow-list.
                follow_redirects=False,
            )
        return self._client


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECT_HOPS = 3


def _raise_for_bad_status(response: httpx.Response) -> None:
    """Raise for retry accounting when a webhook endpoint returns non-2xx."""
    raise httpx.RequestError(
        f"Webhook returned {response.status_code}: {response.text[:200]}",
        request=response.request,
    )


async def _post_with_safe_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    content: bytes,
    headers: dict,
    max_hops: int = _MAX_REDIRECT_HOPS,
) -> httpx.Response:
    """POST with manual redirect handling that re-validates every hop.

    httpx-level redirects are disabled because they would fetch whatever
    Location the server returns, including link-local/metadata addresses
    that validate_public_http_url forbids. Each hop is resolved against
    the previous URL and re-validated; an unsafe target raises UnsafeURL
    without any further request being made.
    """
    from urllib.parse import urljoin

    validate_public_http_url(url)
    method = "POST"
    body: bytes | None = content
    for _ in range(max_hops + 1):
        if method == "POST":
            resp = await client.post(url, content=body, headers=headers)
        else:
            resp = await client.get(url, headers=headers)
        if resp.status_code not in _REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        url = urljoin(url, location)
        validate_public_http_url(url)
        if method == "POST" and resp.status_code in (301, 302, 303):
            method = "GET"
            body = None
    raise httpx.RequestError(f"Too many redirects (>{max_hops} hops)")


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
        validate_public_http_url(webhook.url)
    except UnsafeURL:
        logger.warning("Webhook delivery blocked for unsafe URL: id=%s", webhook_id)
        return {"success": False, "error": "Webhook URL is not allowed"}

    payload_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    signature = hmac.new(
        webhook.secret.encode("utf-8") if webhook.secret else b"",
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()

    attempt_number = (task.request.retries or 0) + 1
    start_ms = int(time.time() * 1000)

    headers = {
        "Content-Type": "application/json",
        "X-PipelineIQ-Event": payload.get("event_type", "unknown"),
        "X-PipelineIQ-Delivery-ID": str(task.request.id),
        "User-Agent": "PipelineIQ/2.12.0",
    }
    if webhook.secret:
        headers["X-PipelineIQ-Signature"] = f"sha256={signature}"

    try:
        resp = await _post_with_safe_redirects(
            task.client,
            webhook.url,
            content=payload_bytes,
            headers=headers,
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
                delivery.delivered_at = datetime.now(UTC)
            else:
                delivery.failed_at = datetime.now(UTC)
                delivery.error_message = f"HTTP {resp.status_code}"
            db.add(delivery)
            db.commit()
        finally:
            db.close()

        if not success:
            _raise_for_bad_status(resp)
    except UnsafeURL as e:
        duration_ms = int(time.time() * 1000) - start_ms
        error_str = f"Redirect target blocked: {e}"

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
            delivery.failed_at = datetime.now(UTC)
            delivery.error_message = error_str
            db.add(delivery)
            db.commit()
        finally:
            db.close()

        logger.warning(
            "Webhook redirect blocked: url=%s, error=%s",
            webhook.url[:60],
            error_str,
        )
        return {"success": False, "error": error_str}

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
            delivery.failed_at = datetime.now(UTC)
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

    return {
        "success": True,
        "status_code": resp.status_code,
        "duration_ms": duration_ms,
    }


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
) -> dict[str, Any]:
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
            "timestamp": datetime.now(UTC).isoformat(),
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
    except Exception as exc:
        logger.exception("Webhook delivery failed for run %s", run_id)
        raise self.retry(exc=exc) from exc
    else:
        return {
            "run_id": run_id,
            "status": "enqueued" if matched > 0 else "skipped",
            "matched_webhooks": matched,
        }


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
