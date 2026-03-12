"""Notification service for pipeline event notifications.

Supports sending notifications to Slack (via webhook) and looking up
matching notification configs for pipeline events.
"""

import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from backend.models import NotificationConfig, NotificationType

logger = logging.getLogger(__name__)


def send_slack_notification(webhook_url: str, message: str) -> bool:
    """Send a message to a Slack webhook URL.

    Returns True on success, False on failure.
    """
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
                response.status_code, response.text[:200],
            )
            return False
    except httpx.HTTPError as exc:
        logger.error("Slack notification error: %s", exc)
        return False


def notify_pipeline_event(
    db: Session,
    event_type: str,
    pipeline_name: str,
    run_id: Optional[str] = None,
    status: Optional[str] = None,
    error_message: Optional[str] = None,
) -> int:
    """Find matching notification configs and send notifications.

    Returns the number of notifications successfully sent.
    """
    configs = (
        db.query(NotificationConfig)
        .filter(NotificationConfig.is_active == True)  # noqa: E712
        .all()
    )

    sent = 0
    for config in configs:
        events_list = config.events or []
        if event_type not in events_list:
            continue

        message = _build_message(event_type, pipeline_name, run_id, status, error_message)

        if config.type == NotificationType.SLACK:
            webhook_url = (config.config or {}).get("slack_webhook_url")
            if webhook_url and send_slack_notification(webhook_url, message):
                sent += 1
        # Email support can be added here in the future

    return sent


def _build_message(
    event_type: str,
    pipeline_name: str,
    run_id: Optional[str],
    status: Optional[str],
    error_message: Optional[str],
) -> str:
    """Build a human-readable notification message."""
    emoji = {"pipeline_completed": "✅", "pipeline_failed": "❌"}.get(event_type, "ℹ️")
    parts = [f"{emoji} *Pipeline Event: {event_type}*"]
    parts.append(f"• Pipeline: {pipeline_name}")
    if run_id:
        parts.append(f"• Run ID: {run_id}")
    if status:
        parts.append(f"• Status: {status}")
    if error_message:
        parts.append(f"• Error: {error_message}")
    return "\n".join(parts)
