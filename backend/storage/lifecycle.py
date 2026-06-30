"""MinIO object lifecycle policies for PipelineIQ buckets.

Without lifecycle policies, MinIO buckets grow without bound. On a laptop
with limited disk, this fills the drive within weeks. These policies define
automatic retention and expiry for each bucket.

Four buckets, four policies:
  pipelineiq-outputs — delete output files older than 7 days
  pipelineiq-spills  — delete Arrow IPC spill files older than 2 days
  pipelineiq-uploads — no auto-deletion (user-managed source data)
  pipelineiq-wasm    — no auto-deletion (long-lived code artifacts)

Policies are applied at application startup and are idempotent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from minio import Minio
from minio.commonconfig import Filter
from minio.lifecycleconfig import Expiration, LifecycleConfig, Rule
from backend.config import settings

logger = logging.getLogger(__name__)

BUCKETS = [
    "pipelineiq-outputs",
    "pipelineiq-spills",
    "pipelineiq-uploads",
    "pipelineiq-wasm",
]


def _minio_client() -> Minio:
    endpoint = (
        os.environ.get("MINIO_ENDPOINT")
        or (settings.S3_ENDPOINT_URL or "").replace("http://", "").replace("https://", "")
    )
    if not endpoint or not settings.S3_ACCESS_KEY or not settings.S3_SECRET_KEY:
        raise RuntimeError("MinIO/S3 credentials are not configured")
    return Minio(
        endpoint,
        access_key=settings.S3_ACCESS_KEY,
        secret_key=settings.S3_SECRET_KEY,
        secure=bool(settings.S3_ENDPOINT_URL and settings.S3_ENDPOINT_URL.startswith("https://")),
    )


def apply_all_lifecycle_policies() -> dict[str, dict[str, Any]]:
    client = _minio_client()
    results: dict[str, dict[str, Any]] = {}

    # Pipeline outputs: delete after 7 days
    try:
        config = LifecycleConfig(rules=[
            Rule(
                rule_id="expire-outputs-7d",
                status="Enabled",
                rule_filter=Filter(prefix="outputs/"),
                expiration=Expiration(days=7),
            )
        ])
        client.set_bucket_lifecycle("pipelineiq-outputs", config)
        results["pipelineiq-outputs"] = {"status": "ok", "policy": "expire after 7 days"}
        logger.info("Lifecycle policy set: pipelineiq-outputs (7-day expiry)")
    except Exception as exc:
        results["pipelineiq-outputs"] = {"status": "error", "error": str(exc)}

    # Pipeline spills: delete after 2 days
    try:
        config = LifecycleConfig(rules=[
            Rule(
                rule_id="expire-spills-2d",
                status="Enabled",
                rule_filter=Filter(prefix="arrow_bus/"),
                expiration=Expiration(days=2),
            )
        ])
        client.set_bucket_lifecycle("pipelineiq-spills", config)
        results["pipelineiq-spills"] = {"status": "ok", "policy": "expire after 2 days"}
        logger.info("Lifecycle policy set: pipelineiq-spills (2-day expiry)")
    except Exception as exc:
        results["pipelineiq-spills"] = {"status": "error", "error": str(exc)}

    # Uploads: user-managed, no auto-expiry
    results["pipelineiq-uploads"] = {"status": "ok", "policy": "no auto-expiry (user-managed)"}
    # Wasm modules: user-managed, no auto-expiry
    results["pipelineiq-wasm"] = {"status": "ok", "policy": "no auto-expiry (user-managed)"}

    return results


def get_lifecycle_policies() -> dict[str, dict[str, Any]]:
    client = _minio_client()
    results: dict[str, dict[str, Any]] = {}

    for bucket in BUCKETS:
        try:
            config = client.get_bucket_lifecycle(bucket)
            if config is None or config.rules is None:
                results[bucket] = {"rules": [], "note": "no lifecycle configured"}
                continue
            rules = []
            for rule in config.rules:
                prefix = None
                if rule.rule_filter and hasattr(rule.rule_filter, "prefix"):
                    prefix = rule.rule_filter.prefix
                rules.append({
                    "id":          rule.rule_id,
                    "status":      rule.status,
                    "prefix":      prefix,
                    "expiry_days": getattr(rule.expiration, "days", None),
                })
            results[bucket] = {"rules": rules}
        except Exception as exc:
            if "NoSuchLifecycleConfiguration" in str(exc):
                results[bucket] = {"rules": [], "note": "no lifecycle configured"}
            else:
                results[bucket] = {"error": str(exc)}

    return results


def get_bucket_storage_stats() -> dict[str, Any]:
    client = _minio_client()
    stats: dict[str, Any] = {}

    for bucket in BUCKETS:
        try:
            objects = list(client.list_objects(bucket, recursive=True))
            if not objects:
                stats[bucket] = {"object_count": 0, "total_bytes": 0, "total_mb": 0}
                continue

            total_bytes = sum(o.size or 0 for o in objects)
            largest = max(objects, key=lambda o: o.size or 0)
            oldest = min(objects, key=lambda o: o.last_modified or datetime.now(timezone.utc))
            top10 = sorted(objects, key=lambda o: o.size or 0, reverse=True)[:10]

            stats[bucket] = {
                "object_count": len(objects),
                "total_bytes":  total_bytes,
                "total_mb":     round(total_bytes / 1_048_576, 1),
                "largest_object": {
                    "name":       largest.object_name,
                    "size_bytes": largest.size,
                    "size_mb":    round((largest.size or 0) / 1_048_576, 1),
                },
                "oldest_object": {
                    "name":           oldest.object_name,
                    "last_modified":  oldest.last_modified.isoformat() if oldest.last_modified else None,
                },
                "top10_by_size": [
                    {"name": o.object_name, "size_mb": round((o.size or 0) / 1_048_576, 1)}
                    for o in top10
                ],
            }
        except Exception as exc:
            stats[bucket] = {"error": str(exc)}

    return stats
