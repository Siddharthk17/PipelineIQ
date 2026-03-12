"""Celery task for asynchronous notification delivery."""

import logging
from typing import Dict

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
) -> Dict[str, str]:
    """Deliver Slack/email notifications for a pipeline event."""
    db = SessionLocal()
    try:
        notify_pipeline_event(
            db=db,
            event_type=event_type,
            pipeline_name=pipeline_name,
            run_id=run_id,
            status=status,
            error_message=error_message,
        )
        return {"run_id": run_id, "status": "delivered"}
    except Exception as exc:
        logger.error("Notification delivery failed for run %s: %s", run_id, exc)
        raise self.retry(exc=exc)
    finally:
        db.close()
