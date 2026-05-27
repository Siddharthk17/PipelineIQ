"""Periodic storage maintenance tasks triggered by Celery beat.

Runs every 5 minutes:
  - Evict hot Redis entries to /dev/shm when cache memory > 90%
  - Clean stale /dev/shm files older than 24 hours
"""

from backend.celery_app import celery_app
from backend.execution.arrow_bus import get_arrow_bus
from backend.execution.shm_store import cleanup_stale


@celery_app.task(
    name="tasks.maintain_storage",
    queue="bulk",
    ignore_result=True,
    serializer="json",
)
def maintain_storage() -> dict:
    bus = get_arrow_bus()
    evicted = bus.maybe_evict_hot_to_warm()
    stale = cleanup_stale()

    return {"evicted": evicted, "stale_shm_deleted": stale}
