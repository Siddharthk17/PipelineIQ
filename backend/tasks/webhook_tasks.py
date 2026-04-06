"""Celery task for asynchronous webhook delivery.

Decouples webhook HTTP calls from pipeline execution so slow/failed
webhooks don't delay result persistence.
"""

import logging
from typing import Dict

from backend.celery_app import celery_app

logger = logging.getLogger(__name__)


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
) -> Dict[str, str]:
    """Deliver webhooks for a pipeline run asynchronously."""
    try:
        from backend.services.webhook_service import trigger_webhooks_for_run

        trigger_webhooks_for_run(
            run_id=run_id,
            status=status,
            pipeline_name=pipeline_name,
            duration_ms=duration_ms,
            steps_count=steps_count,
            rows_processed=rows_processed,
            user_id=user_id,
        )
        return {"run_id": run_id, "status": "delivered"}
    except Exception as exc:
        logger.error("Webhook delivery failed for run %s: %s", run_id, exc)
        raise self.retry(exc=exc)
