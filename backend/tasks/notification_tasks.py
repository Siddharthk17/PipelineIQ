"""Celery task for asynchronous notification delivery."""

import logging
from typing import Any

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.services.notification_service import notify_pipeline_event

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="notifications.deliver",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def deliver_notifications_task(
    self,
    run_id: str,
    event_type: str,
    pipeline_name: str = "",
    status: str = "",
    error_message: str = "",
    user_id: str = "",
) -> dict[str, Any]:
    """Deliver Slack/email notifications for a pipeline event."""
    db = SessionLocal()
    try:
        report = notify_pipeline_event(
            db=db,
            event_type=event_type,
            pipeline_name=pipeline_name,
            run_id=run_id,
            status=status,
            error_message=error_message,
            user_id=user_id,
        )
    except Exception as exc:
        logger.exception(
            "Notification delivery failed for run %s",
            run_id)
        raise self.retry(exc=exc) from exc
    else:
        return {"run_id": run_id, **report}
    finally:
        db.close()
