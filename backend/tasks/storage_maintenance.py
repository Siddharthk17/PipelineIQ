"""Periodic storage maintenance tasks triggered by Celery beat.

Runs every 5 minutes:
  - Evict hot Redis entries to /dev/shm when cache memory > 90%
  - Clean stale /dev/shm files older than 24 hours
"""

import logging
import uuid

from backend.celery_app import celery_app
from backend.db.redis_pools import get_cache_redis
from backend.execution.arrow_bus import get_arrow_bus
from backend.execution.shm_store import cleanup_stale

logger = logging.getLogger(__name__)

STORAGE_MAINTENANCE_LOCK_KEY = "locks:storage:maintenance"
STORAGE_MAINTENANCE_LOCK_TTL_SECONDS = 240


@celery_app.task(
    name="tasks.maintain_storage",
    queue="bulk",
    ignore_result=True,
    serializer="json",
)
def maintain_storage() -> dict:
    lock_token = str(uuid.uuid4())
    try:
        redis = get_cache_redis()
        lock_acquired = bool(
            redis.set(
                STORAGE_MAINTENANCE_LOCK_KEY,
                lock_token,
                nx=True,
                ex=STORAGE_MAINTENANCE_LOCK_TTL_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("Storage maintenance lock unavailable: %s", exc)
        lock_acquired = False

    if not lock_acquired:
        return {"evicted": 0, "stale_shm_deleted": 0, "locked": True}

    bus = get_arrow_bus()
    try:
        evicted = bus.maybe_evict_hot_to_warm()
        stale = cleanup_stale()
    finally:
        try:
            current_token = redis.get(STORAGE_MAINTENANCE_LOCK_KEY)
            if isinstance(current_token, bytes):
                current_token = current_token.decode("utf-8")
            if current_token == lock_token:
                redis.delete(STORAGE_MAINTENANCE_LOCK_KEY)
        except Exception:
            logger.debug("Storage maintenance lock release skipped", exc_info=True)

    return {"evicted": evicted, "stale_shm_deleted": stale, "locked": False}
