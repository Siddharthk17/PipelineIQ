"""Storage analytics API — tier health, bucket sizes, lifecycle status."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.auth import get_current_admin, get_current_user
from backend.dependencies import get_read_db_dependency
from backend.execution.arrow_bus import get_arrow_bus
from backend.models import User
from backend.storage.lifecycle import get_bucket_storage_stats, get_lifecycle_policies

router = APIRouter(prefix="/api/v1/storage", tags=["Storage"])
logger = logging.getLogger(__name__)


@router.get("/stats")
def get_storage_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_read_db_dependency),
):
    try:
        bus = get_arrow_bus()
        tier_stats = bus.get_tier_stats()
    except Exception as exc:
        tier_stats = {"error": str(exc)}

    try:
        bucket_stats = get_bucket_storage_stats()
    except Exception as exc:
        bucket_stats = {"error": str(exc)}

    try:
        lifecycle = get_lifecycle_policies()
    except Exception as exc:
        lifecycle = {"error": str(exc)}

    event_stats: list[dict] = []
    try:
        rows = db.execute(text("""
            SELECT
                tier,
                COUNT(*)          AS event_count,
                SUM(payload_bytes) AS total_bytes,
                AVG(duration_ms)   AS avg_duration_ms
            FROM storage_events
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY tier
            ORDER BY tier
        """)).fetchall()
        for row in rows:
            event_stats.append({
                "tier":            row.tier,
                "event_count":     row.event_count,
                "total_bytes":     int(row.total_bytes or 0),
                "avg_duration_ms": round(float(row.avg_duration_ms or 0), 2),
            })
    except Exception as exc:
        logger.debug("Could not load storage events: %s", exc)

    growth_trend: list[dict] = []
    try:
        rows = db.execute(text("""
            SELECT
                DATE(created_at)                    AS day,
                SUM(payload_bytes) / 1048576.0      AS total_mb
            FROM storage_events
            WHERE event_type LIKE 'write_%'
              AND created_at > NOW() - INTERVAL '7 days'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """)).fetchall()
        for row in rows:
            growth_trend.append({
                "day": str(row.day),
                "mb":  round(float(row.total_mb or 0), 1),
            })
    except Exception as exc:
        logger.debug("Could not load growth trend: %s", exc)

    return {
        "tiers":           tier_stats,
        "buckets":         bucket_stats,
        "lifecycle":       lifecycle,
        "event_stats":     event_stats,
        "growth_trend_7d": growth_trend,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
    }


@router.get("/tier-health")
def get_tier_health(
    current_user: User = Depends(get_current_user),
):
    warnings: list[dict] = []

    try:
        bus = get_arrow_bus()
        stats = bus.get_tier_stats()

        hot = stats.get("hot", {})
        if isinstance(hot, dict) and "utilization" in hot:
            util = hot["utilization"]
            if util > 0.95:
                warnings.append({
                    "tier": "hot", "severity": "critical",
                    "message": f"Redis cache at {util*100:.0f}%",
                })
            elif util > 0.85:
                warnings.append({
                    "tier": "hot", "severity": "warning",
                    "message": f"Redis cache at {util*100:.0f}%",
                })

        warm = stats.get("warm", {})
        if isinstance(warm, dict) and "utilization" in warm:
            util = warm["utilization"]
            if util > 0.80:
                warnings.append({
                    "tier": "warm", "severity": "warning",
                    "message": f"/dev/shm at {util*100:.0f}%",
                })

    except Exception as exc:
        warnings.append({"tier": "unknown", "severity": "error", "message": str(exc)})

    return {"healthy": len(warnings) == 0, "warnings": warnings}


@router.post("/evict")
def trigger_eviction(
    current_user: User = Depends(get_current_admin),
):
    bus = get_arrow_bus()
    evicted = bus.maybe_evict_hot_to_warm()
    return {"evicted": evicted}


@router.post("/cleanup-stale-shm")
def cleanup_stale_shm(
    current_user: User = Depends(get_current_admin),
):
    from backend.execution.shm_store import cleanup_stale
    deleted = cleanup_stale()
    return {"deleted": deleted}
