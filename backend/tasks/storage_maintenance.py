"""Periodic storage maintenance tasks triggered by Celery beat.

Runs every 5 minutes:
  - Evict hot Redis entries to /dev/shm when cache memory > 90%
  - Clean stale /dev/shm files older than 24 hours
"""

import logging
import os
import time
import uuid
from pathlib import Path

from backend.celery_app import celery_app
from backend.config import settings
from backend.database import SessionLocal
from backend.db.redis_pools import get_cache_redis
from backend.execution.arrow_bus import get_arrow_bus
from backend.execution.shm_store import cleanup_stale
from backend.models import UploadedFile

logger = logging.getLogger(__name__)

STORAGE_MAINTENANCE_LOCK_KEY = "locks:storage:maintenance"
STORAGE_MAINTENANCE_LOCK_TTL_SECONDS = 240
DIRECT_UPLOAD_TTL_SECONDS = 30 * 60


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
        return {
            "evicted": 0,
            "stale_shm_deleted": 0,
            "orphan_uploads_deleted": 0,
            "locked": True,
        }

    bus = get_arrow_bus()
    try:
        evicted = bus.maybe_evict_hot_to_warm()
        stale = cleanup_stale()
        orphan_uploads = _cleanup_orphaned_local_uploads()
    finally:
        try:
            current_token = redis.get(STORAGE_MAINTENANCE_LOCK_KEY)
            if isinstance(current_token, bytes):
                current_token = current_token.decode("utf-8")
            if current_token == lock_token:
                redis.delete(STORAGE_MAINTENANCE_LOCK_KEY)
        except Exception:
            logger.debug("Storage maintenance lock release skipped", exc_info=True)

    return {
        "evicted": evicted,
        "stale_shm_deleted": stale,
        "orphan_uploads_deleted": orphan_uploads,
        "locked": False,
    }


def _cleanup_orphaned_local_uploads() -> int:
    """Delete expired local direct-upload staging files not present in DB."""
    if settings.STORAGE_TYPE != "local":
        return 0

    upload_dir = Path(settings.UPLOAD_DIR)
    if not upload_dir.exists():
        return 0

    db = SessionLocal()
    try:
        stored_names = {
            os.path.basename(str(row[0]))
            for row in db.query(UploadedFile.stored_path).all()
            if row[0]
        }
    finally:
        db.close()

    deleted = 0
    now = time.time()
    for path in upload_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in settings.ALLOWED_EXTENSIONS:
            continue
        try:
            uuid.UUID(path.stem)
        except ValueError:
            continue
        if path.name in stored_names:
            continue
        try:
            if now - path.stat().st_mtime < DIRECT_UPLOAD_TTL_SECONDS:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            logger.debug("Failed to delete orphan upload %s", path, exc_info=True)
    if deleted:
        logger.info("Deleted %d orphaned local upload files", deleted)
    return deleted
