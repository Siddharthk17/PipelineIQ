"""Periodic storage maintenance tasks triggered by Celery beat.

Runs every 5 minutes:
  - Evict hot Redis entries to /dev/shm when cache memory > 90%
  - Clean stale /dev/shm files older than 24 hours

Runs daily:
  - Sweep orphaned upload rows (M-107): metadata older than the retention
    horizon whose storage object is already gone, or whose upload never
    finalized (no snapshot and no profile). Deletes run children-first so
    the parent-row delete never hits an FK violation.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.db.redis_pools import get_cache_redis
from backend.execution.arrow_bus import get_arrow_bus
from backend.execution.shm_store import cleanup_stale
from backend.models import FileProfile, SchemaSnapshot, UploadedFile
from backend.models.column_policy import ColumnPolicy
from backend.services import storage_service as storage_service_module
from backend.tasks.retry_helpers import retry_sync

logger = logging.getLogger(__name__)

STORAGE_MAINTENANCE_LOCK_KEY = "locks:storage:maintenance"
STORAGE_MAINTENANCE_LOCK_TTL_SECONDS = 240

ORPHAN_SWEEP_LOCK_KEY = "locks:storage:orphan_sweep"
ORPHAN_SWEEP_LOCK_TTL_SECONDS = 600
# Rows newer than this are never sweep candidates, even when their object
# is missing (uploads in flight, eventual-consistency windows).
ORPHAN_RETENTION_DAYS = 30
ORPHAN_SWEEP_BATCH_LIMIT = 100


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


def _is_orphan_candidate(db, row, object_exists: bool) -> bool:
    """True when an old row shows evidence of abandonment (M-107).

    A row is swept only if it is past the retention horizon (checked by the
    caller) AND either its storage object is already gone, or the upload
    never finalized (no schema snapshot and no computed profile). Rows
    with live objects and intact metadata are always kept: "old" alone is
    never enough to delete user data.
    """
    if not object_exists:
        return True
    has_snapshot = (
        db.query(SchemaSnapshot.id).filter_by(file_id=row.id).first() is not None
    )
    if has_snapshot:
        return False
    has_profile = (
        db.query(FileProfile.id).filter_by(file_id=row.id).first() is not None
    )
    return not has_profile


def _delete_upload_row(db, row) -> None:
    """Delete one upload and its dependents, children first (M-107).

    ``SchemaSnapshot.file_id`` has no DB-level ``ON DELETE`` action, so
    snapshots must be deleted explicitly before the parent row; profiles
    and policies (DB CASCADE) are deleted explicitly too so the ordering
    holds on every backend. The storage object goes before the parent
    row: if the object delete fails the row survives for the next sweep
    instead of leaking an unreferenced object.
    """
    storage = storage_service_module.storage_service
    db.query(SchemaSnapshot).filter_by(file_id=row.id).delete(
        synchronize_session=False
    )
    db.query(FileProfile).filter_by(file_id=row.id).delete(
        synchronize_session=False
    )
    db.query(ColumnPolicy).filter_by(file_id=row.id).delete(
        synchronize_session=False
    )
    try:
        retry_sync(
            lambda: storage.delete(row.stored_path),
            attempts=3,
            exceptions=(Exception,),
            base_seconds=1.0,
            max_seconds=10.0,
            action=f"storage delete {row.stored_path}",
        )
    except Exception:
        # The object may already be gone (lifecycle expiry, manual
        # cleanup): never strand the metadata row over a missing object.
        logger.warning(
            "Storage delete failed for orphan %s; removing metadata anyway",
            row.id,
        )
    db.delete(row)


@celery_app.task(
    name="tasks.sweep_orphaned_uploads",
    queue="bulk",
    ignore_result=True,
    serializer="json",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    max_retries=3,
)
def sweep_orphaned_uploads(self) -> dict:
    """Delete retention-aged, demonstrably-abandoned upload rows (M-107)."""
    lock_token = str(uuid.uuid4())
    try:
        redis = get_cache_redis()
        lock_acquired = bool(
            redis.set(
                ORPHAN_SWEEP_LOCK_KEY,
                lock_token,
                nx=True,
                ex=ORPHAN_SWEEP_LOCK_TTL_SECONDS,
            )
        )
    except Exception as exc:
        logger.warning("Orphan-sweep lock unavailable: %s", exc)
        lock_acquired = False

    if not lock_acquired:
        return {"deleted": 0, "scanned": 0, "locked": True}

    cutoff = datetime.now(UTC) - timedelta(days=ORPHAN_RETENTION_DAYS)
    deleted = 0
    scanned = 0
    db = SessionLocal()
    try:
        candidates = (
            db.query(UploadedFile)
            .filter(UploadedFile.created_at < cutoff)
            .order_by(UploadedFile.created_at.asc())
            .limit(ORPHAN_SWEEP_BATCH_LIMIT)
            .all()
        )
        scanned = len(candidates)
        storage = storage_service_module.storage_service
        for row in candidates:
            try:
                try:
                    object_exists = bool(storage.exists(row.stored_path))
                except Exception:
                    logger.warning(
                        "Storage exists-check failed for %s; skipping",
                        row.stored_path,
                    )
                    continue
                if not _is_orphan_candidate(db, row, object_exists):
                    continue
                _delete_upload_row(db, row)
                db.commit()
                deleted += 1
            except Exception:
                db.rollback()
                logger.exception("Failed to sweep orphaned upload %s", row.id)
    finally:
        try:
            db.close()
        except Exception:
            pass
        try:
            current_token = redis.get(ORPHAN_SWEEP_LOCK_KEY)
            if isinstance(current_token, bytes):
                current_token = current_token.decode("utf-8")
            if current_token == lock_token:
                redis.delete(ORPHAN_SWEEP_LOCK_KEY)
        except Exception:
            logger.debug("Orphan-sweep lock release skipped", exc_info=True)

    return {"deleted": deleted, "scanned": scanned, "locked": False}
