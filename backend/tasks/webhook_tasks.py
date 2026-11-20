"""Celery task for asynchronous webhook delivery.

Each delivery creates its own httpx.AsyncClient inside the task's event
loop and closes it before the loop is torn down. A shared (pooled) client
must NOT be reused across deliveries: every task run executes on a fresh
event loop, and a client bound to an earlier, now-closed loop fails with
``Event loop is closed`` while leaving no delivery record.

Retry policy: only transient failures are retried — HTTP 429 / 5xx and
timeouts, via Celery autoretry with exponential backoff and jitter.
Permanent 4xx, DNS/validation rejections and other transport errors are
recorded once as dead-letter outcomes without retry. Every attempt writes
exactly one delivery record.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
from celery import Task

from backend.celery_app import celery_app
from backend.utils.url_security import (
    UnsafeURL,
    build_webhook_signature,
    pin_url_to_validated_ip,
    validate_public_http_url,
)

logger = logging.getLogger(__name__)


class WebhookTask(Task):
    """Celery base for webhook delivery tasks.

    HTTP clients are deliberately NOT shared here: use :func:`_make_client`
    to create a per-delivery client inside the running event loop.
    """


def _make_client() -> httpx.AsyncClient:
    """Create a fresh async HTTP client for a single delivery attempt."""
    return httpx.AsyncClient(
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
        # so every hop can be re-validated and re-pinned. Never enable
        # httpx-level redirects here: they would fetch whatever Location
        # the server returns, including link-local/metadata addresses
        # that validate_public_http_url forbids.
        follow_redirects=False,
    )


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECT_HOPS = 3


def _is_retryable_status(status_code: int) -> bool:
    """Only 429 and 5xx are transient. All other statuses are final."""
    return status_code == 429 or 500 <= status_code <= 599


def _pinned_request(
    url: str, headers: dict, *, strict: bool
) -> tuple[str, dict, dict]:
    """Validate ``url`` and pin it to a validated IP.

    Returns ``(request_url, request_headers, extensions)``. ``request_url``
    targets a validated IP literal while ``request_headers`` preserves the
    original ``Host``. When pinning is impossible (the hostname cannot be
    resolved to a validated address right now):

    * strict mode (real httpx clients) fails closed with UnsafeURL — the
      connection target cannot be proven safe;
    * non-strict mode (test doubles without real DNS) falls back to the
      validated URL so canned-response fakes keep working. The URL was
      still validated by :func:`validate_public_http_url` first.
    """
    validate_public_http_url(url)
    try:
        pinned_url, host_header, extensions = pin_url_to_validated_ip(url)
    except UnsafeURL:
        if strict:
            raise
        return url, dict(headers), {}
    request_headers = dict(headers)
    request_headers["Host"] = host_header
    return pinned_url, request_headers, extensions


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
    that validate_public_http_url forbids. Each redirect hop is resolved
    against the previous URL, re-validated, and re-pinned to a validated
    IP; an unsafe target raises UnsafeURL without any request being made
    to it.
    """
    from urllib.parse import urljoin

    strict = isinstance(client, httpx.AsyncClient)
    request_url, request_headers, extensions = _pinned_request(
        url, headers, strict=strict
    )
    extra: dict = {"extensions": extensions} if strict and extensions else {}
    method = "POST"
    body: bytes | None = content
    for _ in range(max_hops + 1):
        if method == "POST":
            resp = await client.post(
                request_url, content=body, headers=request_headers, **extra
            )
        else:
            resp = await client.get(request_url, headers=request_headers, **extra)
        if resp.status_code not in _REDIRECT_STATUSES:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        url = urljoin(url, location)
        request_url, request_headers, extensions = _pinned_request(
            url, headers, strict=strict
        )
        extra = {"extensions": extensions} if strict and extensions else {}
        if method == "POST" and resp.status_code in (301, 302, 303):
            method = "GET"
            body = None
    raise httpx.RequestError(f"Too many redirects (>{max_hops} hops)")


def _record_delivery(
    *,
    webhook_id: str,
    run_id: Any,
    event_type: str,
    payload: dict,
    attempt_number: int,
    duration_ms: int,
    response_status: int | None = None,
    response_body: str | None = None,
    delivered: bool = False,
    error_message: str | None = None,
) -> None:
    """Write exactly one delivery record for an attempt.

    Failures here are logged, never raised: record-keeping must not mask
    the delivery outcome it describes.
    """
    from backend.database import SessionLocal
    from backend.models import WebhookDelivery

    db = SessionLocal()
    try:
        delivery = WebhookDelivery(
            webhook_id=webhook_id,
            run_id=run_id,
            event_type=event_type,
            payload=payload,
            response_status=response_status,
            response_body=response_body,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
        )
        if delivered:
            delivery.delivered_at = datetime.now(UTC)
        else:
            delivery.failed_at = datetime.now(UTC)
            delivery.error_message = error_message
        db.add(delivery)
        db.commit()
    except Exception:
        logger.exception("Failed to record webhook delivery for %s", webhook_id)
        with contextlib.suppress(Exception):
            db.rollback()
    finally:
        with contextlib.suppress(Exception):
            db.close()


def _coerce_run_id(payload: dict) -> Any:
    """Best-effort run_id extraction; invalid values degrade to None."""
    run_id = payload.get("data", {}).get("run_id")
    if run_id is None or isinstance(run_id, str):
        return run_id
    try:
        from backend.utils.uuid_utils import as_uuid as _as_uuid

        return _as_uuid(str(run_id))
    except Exception:
        return None


def _coerce_user_id(user_id: str | None) -> Any:
    """Best-effort user_id coercion for webhook owner filters (M-162).

    Callers pass ``str(user.id)``, but ``Webhook.user_id`` is a ``Uuid``
    column: comparing a raw string raises on SQLite (PostgreSQL coerces
    it). Unparseable values degrade to None (no-match), never raise —
    fan-out must skip, not 500.
    """
    if not user_id:
        return None
    try:
        from backend.utils.uuid_utils import as_uuid as _as_uuid

        return _as_uuid(user_id)
    except Exception:
        logger.warning("Dropping unparseable webhook user_id: %r", user_id)
        return None


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
    from backend.models import Webhook

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
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=(task.request.retries or 0) + 1,
            duration_ms=0,
            error_message="Webhook URL is not allowed",
        )
        return {"success": False, "error": "Webhook URL is not allowed"}

    payload_bytes = orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)
    signature = build_webhook_signature(
        webhook.secret if isinstance(webhook.secret, str) else None,
        payload_bytes,
    )

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

    # A fresh client per attempt: it is created and closed inside this
    # event loop, so no connection ever outlives its loop (M-043).
    try:
        async with _make_client() as client:
            resp = await _post_with_safe_redirects(
                client,
                webhook.url,
                content=payload_bytes,
                headers=headers,
            )
    except UnsafeURL as e:
        duration_ms = int(time.time() * 1000) - start_ms
        error_str = f"Redirect target blocked: {e}"
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            error_message=error_str,
        )
        logger.warning(
            "Webhook redirect blocked: url=%s, error=%s",
            webhook.url[:60],
            error_str,
        )
        return {"success": False, "error": error_str}

    except httpx.TimeoutException as e:
        # Transient: record this attempt, then let Celery retry with
        # backoff + jitter.
        duration_ms = int(time.time() * 1000) - start_ms
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            error_message=str(e)[:500],
        )
        logger.warning(
            "Webhook delivery timed out (attempt %d): url=%s",
            attempt_number,
            webhook.url[:60],
        )
        raise

    except httpx.RequestError as e:
        # Non-timeout transport errors (refused, reset, TLS, redirect
        # loops) are deterministic for this attempt: dead-letter them
        # without retrying.
        duration_ms = int(time.time() * 1000) - start_ms
        error_str = str(e)[:500]
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            error_message=error_str,
        )
        logger.warning(
            "Webhook delivery failed (attempt %d): url=%s, error=%s",
            attempt_number,
            webhook.url[:60],
            error_str,
        )
        return {"success": False, "error": error_str}

    except Exception as e:
        # Anything unexpected (including event-loop/pool misuse): always
        # record, then retry — a missing record is worse than a retry.
        duration_ms = int(time.time() * 1000) - start_ms
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            error_message=str(e)[:500],
        )
        logger.warning(
            "Webhook delivery errored (attempt %d): url=%s, error=%s",
            attempt_number,
            webhook.url[:60],
            str(e)[:200],
        )
        raise

    duration_ms = int(time.time() * 1000) - start_ms
    success = 200 <= resp.status_code < 300

    if success:
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            response_status=resp.status_code,
            response_body=resp.text[:1000] if resp.text else None,
            delivered=True,
        )
        logger.info(
            "Webhook delivered: url=%s, status=%d, duration=%dms",
            webhook.url[:60],
            resp.status_code,
            duration_ms,
        )
        return {
            "success": True,
            "status_code": resp.status_code,
            "duration_ms": duration_ms,
        }

    if _is_retryable_status(resp.status_code):
        # Transient: record this attempt, then raise into Celery's
        # backoff+jitter retry. The final exhausted attempt's record is
        # the dead letter.
        _record_delivery(
            webhook_id=webhook_id,
            run_id=_coerce_run_id(payload),
            event_type=payload.get("event_type", "unknown"),
            payload=payload,
            attempt_number=attempt_number,
            duration_ms=duration_ms,
            response_status=resp.status_code,
            response_body=resp.text[:1000] if resp.text else None,
            error_message=f"HTTP {resp.status_code} (retryable)",
        )
        raise httpx.RequestError(
            f"Webhook returned {resp.status_code}: {resp.text[:200]}",
            request=resp.request,
        )

    # Permanent 4xx (and other non-retryable statuses): dead-letter with
    # a single record — retrying would fan out doomed attempts.
    _record_delivery(
        webhook_id=webhook_id,
        run_id=_coerce_run_id(payload),
        event_type=payload.get("event_type", "unknown"),
        payload=payload,
        attempt_number=attempt_number,
        duration_ms=duration_ms,
        response_status=resp.status_code,
        response_body=resp.text[:1000] if resp.text else None,
        error_message=f"HTTP {resp.status_code}",
    )
    return {
        "success": False,
        "error": f"HTTP {resp.status_code}",
        "status_code": resp.status_code,
        "retryable": False,
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
    HTTP calls happen in separate worker tasks. A fan-out enqueue
    failure retries the enqueue only; already-enqueued deliveries are
    unaffected and per-webhook outcomes are recorded by deliver_webhook.
    """
    try:
        from backend.database import SessionLocal
        from backend.models import Webhook

        if user_id and _coerce_user_id(user_id) is None:
            # Unparseable owner id: nothing can match, and broadening the
            # filter to all users would misdeliver (M-162).
            return {
                "run_id": run_id,
                "status": "skipped",
                "matched_webhooks": 0,
            }
        db = SessionLocal()
        try:
            query = db.query(Webhook).filter(Webhook.is_active)
            if user_id:
                query = query.filter(
                    Webhook.user_id == _coerce_user_id(user_id))
            webhooks = query.all()
        except Exception:
            # Session cleanup on query failure (M-173).
            db.rollback()
            raise
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

    owner_id = _coerce_user_id(user_id)
    if owner_id is None:
        # Unparseable owner id: nothing can match (M-162).
        return

    from backend.database import SessionLocal
    from backend.models import Webhook

    db = SessionLocal()
    try:
        webhooks = (
            db.query(Webhook)
            .filter(Webhook.user_id == owner_id, Webhook.is_active.is_(True))
            .all()
        )
    except Exception:
        # Session cleanup on query failure (M-173).
        db.rollback()
        raise
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
