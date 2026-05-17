"""Notification service for pipeline event notifications.

Supports sending notifications to Slack (via webhook) and looking up
matching notification configs for pipeline events.
"""

import logging
import smtplib
import ssl
from typing import Any, Optional

import httpx
from email.message import EmailMessage
from sqlalchemy.orm import Session

from backend.config import settings
from backend.models import NotificationConfig, NotificationType

logger = logging.getLogger(__name__)


def send_slack_notification(webhook_url: str, message: str) -> bool:
    """Send a message to a Slack webhook URL.

    Returns True on success, False on failure.
    """
    if not webhook_url:
        logger.info("Slack notification skipped: webhook URL not configured")
        return False
    try:
        response = httpx.post(
            webhook_url,
            json={"text": message},
            timeout=10.0,
        )
        if response.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.warning(
                "Slack notification failed: status=%d body=%s",
                response.status_code,
                response.text[:200],
            )
            return False
    except httpx.HTTPError as exc:
        logger.error("Slack notification error: %s", exc)
        return False


def send_email_notification(
        recipients: list[str],
        subject: str,
        body: str) -> bool:
    """Send a plain-text email notification via SMTP."""
    if not recipients:
        logger.info("Email notification skipped: no recipients provided")
        return False
    if not settings.SMTP_HOST:
        logger.info("Email notification skipped: SMTP_HOST is not configured")
        return False
    if not settings.SMTP_FROM:
        logger.info("Email notification skipped: SMTP_FROM is not configured")
        return False

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    try:
        if settings.SMTP_USE_SSL:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT,
                context=context,
            ) as server:
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(message)
        else:
            with smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT,
            ) as server:
                server.ehlo()
                if settings.SMTP_USE_TLS:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(message)
        logger.info("Email notification sent to %s", ", ".join(recipients))
        return True
    except Exception as exc:
        logger.error("Email notification error: %s", exc)
        return False


def _normalize_email_recipients(config: dict) -> list[str]:
    value = (config or {}).get("email_to")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    return []


def _delivery_status(
    *,
    matched_configs: int,
    attempted: int,
    sent: int,
    failures: int,
    skipped: int,
) -> str:
    if sent > 0 and failures == 0 and skipped == 0:
        return "delivered"
    if sent > 0 and (failures > 0 or skipped > 0):
        return "partial"
    if matched_configs == 0 or attempted == 0:
        return "skipped"
    if failures > 0:
        return "failed"
    return "skipped"


def notify_pipeline_event(
    db: Session,
    event_type: str,
    pipeline_name: str,
    run_id: Optional[str] = None,
    status: Optional[str] = None,
    error_message: Optional[str] = None,
    user_id: str = "",
) -> dict[str, Any]:
    """Find matching notification configs and send notifications.

    Returns the number of notifications successfully sent.
    """
    query = db.query(NotificationConfig).filter(NotificationConfig.is_active)
    if user_id:
        query = query.filter(NotificationConfig.user_id == user_id)
    configs = query.all()

    matched_configs = 0
    attempted = 0
    sent = 0
    failures = 0
    skipped = 0
    for config in configs:
        events_list = config.events or []
        if event_type not in events_list:
            continue
        matched_configs += 1

        message = _build_message(
            event_type, pipeline_name, run_id, status, error_message
        )

        if config.type == NotificationType.SLACK:
            attempted += 1
            webhook_url = (config.config or {}).get("slack_webhook_url")
            if not webhook_url:
                skipped += 1
                logger.info("Slack notification skipped: webhook URL not configured")
            elif send_slack_notification(webhook_url, message):
                sent += 1
            else:
                failures += 1
        elif config.type == NotificationType.EMAIL:
            attempted += 1
            recipients = _normalize_email_recipients(config.config or {})
            if not recipients:
                skipped += 1
                logger.info("Email notification skipped: no recipients provided")
            else:
                subject = f"PipelineIQ: {event_type.replace('_', ' ').title()}"
                if send_email_notification(recipients, subject, message):
                    sent += 1
                else:
                    failures += 1

    return {
        "status": _delivery_status(
            matched_configs=matched_configs,
            attempted=attempted,
            sent=sent,
            failures=failures,
            skipped=skipped,
        ),
        "matched_configs": matched_configs,
        "attempted": attempted,
        "sent": sent,
        "failed": failures,
        "skipped": skipped,
    }


def _build_message(
    event_type: str,
    pipeline_name: str,
    run_id: Optional[str],
    status: Optional[str],
    error_message: Optional[str],
) -> str:
    """Build a human-readable notification message."""
    emoji = {
        "pipeline_completed": "✅",
        "pipeline_failed": "❌"}.get(
        event_type,
        "ℹ️")
    parts = [f"{emoji} *Pipeline Event: {event_type}*"]
    parts.append(f"• Pipeline: {pipeline_name}")
    if run_id:
        parts.append(f"• Run ID: {run_id}")
    if status:
        parts.append(f"• Status: {status}")
    if error_message:
        parts.append(f"• Error: {error_message}")
    return "\n".join(parts)
