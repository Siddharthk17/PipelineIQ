"""Tiered Arrow IPC data bus for inter-step execution transport.

Routing contract:
  < 10MB   -> Redis (raw Arrow IPC bytes, TTL 1h)
  10-500MB -> /dev/shm (Arrow IPC file, deleted on run completion)
  >= 500MB -> spill storage (Parquet+Snappy, TTL 48h)

The bus automatically demotes large Redis entries to /dev/shm when Redis
memory exceeds 90% capacity. All three tiers are purged on run completion.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import orjson
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
from redis.exceptions import RedisError

from backend.db.redis_pools import get_cache_redis_binary
from backend.execution import shm_store
from backend.services.storage_service import storage_service
from backend.storage.event_logger import log_storage_event

logger = logging.getLogger(__name__)

REDIS_THRESHOLD = 10 * 1024 * 1024   # 10MB
SHM_THRESHOLD = 500 * 1024 * 1024    # 500MB
SHM_PREFIX = "piq_"
SHM_DIR = "/dev/shm"  # nosec B108 - intentional shared-memory mount for Arrow IPC

REDIS_EVICT_THRESHOLD = 0.90  # Start evicting when Redis > 90% full

_SAFE_KEY_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class _ArrowLocation:
    key: str
    run_id: str
    tier: str
    pointer: Optional[str] = None
    size_bytes: int = 0


class ArrowDataBus:
    """Store pyarrow Tables using Redis, shared memory, or spill storage."""

    def __init__(
        self,
        *,
        small_threshold_mb: float = 10.0,
        medium_threshold_mb: float = 500.0,
        redis_ttl_seconds: int = 3600,
        manifest_ttl_seconds: int = 86400,
        redis_client=None,
        disk_prefix: str = "arrow_bus",
        shm_dir: str = SHM_DIR,
    ) -> None:
        if small_threshold_mb <= 0:
            raise ValueError("small_threshold_mb must be > 0")
        if medium_threshold_mb <= small_threshold_mb:
            raise ValueError(
                "medium_threshold_mb must be > small_threshold_mb")
        if redis_ttl_seconds <= 0:
            raise ValueError("redis_ttl_seconds must be > 0")
        if manifest_ttl_seconds <= 0:
            raise ValueError("manifest_ttl_seconds must be > 0")

        self._small_threshold_bytes = int(small_threshold_mb * 1024 * 1024)
        self._medium_threshold_bytes = int(medium_threshold_mb * 1024 * 1024)
        self._redis_ttl_seconds = redis_ttl_seconds
        self._manifest_ttl_seconds = max(
            redis_ttl_seconds, manifest_ttl_seconds)
        self._disk_prefix = disk_prefix.strip("/")
        self._locations: Dict[str, _ArrowLocation] = {}
        self._lock = threading.RLock()
        self._redis = redis_client
        self._redis_retry_after = 0.0
        self._shm_dir = Path(shm_dir)
        self._shm_available = self._ensure_shm_dir()

    def _ensure_shm_dir(self) -> bool:
        if not shm_store.shm_available():
            logger.warning("Shared-memory path unavailable: %s", self._shm_dir)
            return False
        return True

    def _ensure_redis(self):
        if self._redis is not None:
            return self._redis
        if time.monotonic() < self._redis_retry_after:
            return None

        try:
            shared_client = get_cache_redis_binary()
            decode_responses = (
                shared_client.connection_pool.connection_kwargs.get(
                    "decode_responses", False
                )
            )
            if not decode_responses:
                self._redis = shared_client
                self._redis_retry_after = 0.0
                return self._redis
        except Exception:
            pass

        self._redis = None
        self._redis_retry_after = time.monotonic() + 15.0
        return self._redis

    @staticmethod
    def _estimated_size(table: pa.Table) -> int:
        return max(1, int(table.nbytes))

    @staticmethod
    def _table_to_bytes(table: pa.Table) -> bytes:
        sink = pa.BufferOutputStream()
        with ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        return sink.getvalue().to_pybytes()

    @staticmethod
    def _bytes_to_table(payload: bytes) -> pa.Table:
        reader = ipc.open_stream(pa.py_buffer(payload))
        return reader.read_all()

    @staticmethod
    def to_ipc_bytes(table: pa.Table) -> bytes:
        """Serialize a table to Arrow IPC bytes."""
        return ArrowDataBus._table_to_bytes(table)

    @staticmethod
    def from_ipc_bytes(payload: bytes) -> pa.Table:
        """Deserialize Arrow IPC bytes into a table."""
        return ArrowDataBus._bytes_to_table(payload)

    def _redis_key(self, run_id: str, key: str) -> str:
        return f"arrow_bus:{run_id}:{key}"

    def _manifest_key(self, run_id: str) -> str:
        return f"arrow_bus:manifest:{run_id}"

    @staticmethod
    def _safe_key(key: str) -> str:
        return _SAFE_KEY_CHARS.sub("_", key).strip("_") or "step"

    def _shm_path(self, run_id: str, key: str) -> Path:
        return shm_store.shm_path_for(run_id, key)

    def _spill_pointer(self, run_id: str, key: str) -> str:
        safe_key = self._safe_key(key)
        filename = f"{self._disk_prefix}_{run_id}_{safe_key}_{uuid.uuid4().hex}.parquet"
        return f"{self._disk_prefix}/{run_id}/{filename}"

    @staticmethod
    def _decode_redis_value(value: object) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    def _record_location(self, location: _ArrowLocation) -> None:
        with self._lock:
            self._locations[location.key] = location
        self._persist_manifest_entry(location)

    def _persist_manifest_entry(self, location: _ArrowLocation) -> None:
        redis_client = self._ensure_redis()
        if redis_client is None:
            return

        payload = orjson.dumps(
            {
                "tier": location.tier,
                "pointer": location.pointer,
                "size_bytes": location.size_bytes,
            }
        )
        try:
            manifest_key = self._manifest_key(location.run_id)
            redis_client.hset(manifest_key, location.key, payload)
            redis_client.expire(manifest_key, self._manifest_ttl_seconds)
        except RedisError:
            logger.warning(
                "Failed to persist Arrow bus manifest entry for run_id=%s key=%s",
                location.run_id,
                location.key,
            )

    def _remove_manifest_entry(self, run_id: str, key: str) -> None:
        redis_client = self._ensure_redis()
        if redis_client is None:
            return

        manifest_key = self._manifest_key(run_id)
        try:
            redis_client.hdel(manifest_key, key)
            if redis_client.hlen(manifest_key) == 0:
                redis_client.delete(manifest_key)
        except RedisError:
            logger.warning(
                "Failed to remove Arrow bus manifest entry for run_id=%s key=%s",
                run_id,
                key,
            )

    def _load_manifest_locations(self, run_id: str) -> list[_ArrowLocation]:
        redis_client = self._ensure_redis()
        if redis_client is None:
            return []

        try:
            entries = redis_client.hgetall(self._manifest_key(run_id))
        except RedisError:
            logger.warning(
                "Failed to load Arrow bus manifest for run_id=%s", run_id)
            return []

        locations: list[_ArrowLocation] = []
        for raw_key, raw_payload in entries.items():
            try:
                key = self._decode_redis_value(raw_key)
                payload = orjson.loads(raw_payload)
                tier = str(payload.get("tier", "")).lower()
                if tier not in {"redis", "shm", "spill"}:
                    continue
                locations.append(
                    _ArrowLocation(
                        key=key,
                        run_id=run_id,
                        tier=tier,
                        pointer=payload.get("pointer"),
                        size_bytes=int(payload.get("size_bytes", 0)),
                    )
                )
            except (ValueError, TypeError, orjson.JSONDecodeError):
                logger.warning(
                    "Skipping malformed Arrow bus manifest entry for run_id=%s", run_id, )
        return locations

    def _clear_manifest(self, run_id: str) -> None:
        redis_client = self._ensure_redis()
        if redis_client is None:
            return
        try:
            redis_client.delete(self._manifest_key(run_id))
        except RedisError:
            logger.warning(
                "Failed to clear Arrow bus manifest for run_id=%s", run_id)

    def _delete_location_payload(self, location: _ArrowLocation) -> None:
        if location.tier == "redis" and location.pointer:
            redis_client = self._ensure_redis()
            if redis_client is not None:
                try:
                    redis_client.delete(location.pointer)
                except RedisError:
                    logger.warning(
                        "Failed to delete Arrow bus redis key %s",
                        location.pointer)
            return

        if location.tier == "shm" and location.pointer:
            try:
                Path(location.pointer).unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to delete Arrow bus shm file %s",
                    location.pointer)
            return

        if location.tier == "spill" and location.pointer:
            try:
                storage_service.delete(location.pointer)
            except Exception:
                logger.warning(
                    "Failed to delete Arrow bus spill key %s", location.pointer
                )

    def _cleanup_orphaned_shm_files(self, run_id: str) -> None:
        deleted = shm_store.remove_for_run(run_id)
        if deleted:
            logger.debug("Cleaned %d orphaned shm files for run_id=%s", deleted, run_id)

    def _cleanup_orphaned_local_spill_files(self, run_id: str) -> None:
        provider = getattr(storage_service, "provider", None)
        base_dir = getattr(provider, "base_dir", None)
        if not isinstance(base_dir, Path):
            return

        pattern = f"{self._disk_prefix}_{run_id}_*.parquet"
        for spill_file in base_dir.glob(pattern):
            try:
                spill_file.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "Failed to delete orphaned spill file %s",
                    spill_file)

    def put(self, key: str, table: pa.Table, *, run_id: str) -> str:
        """Store a table and return the selected tier."""
        if not key:
            raise ValueError("Arrow bus key cannot be empty")
        if not isinstance(table, pa.Table):
            raise ValueError("Arrow bus accepts only pyarrow.Table inputs")

        self.delete(key)
        size_bytes = self._estimated_size(table)
        if size_bytes <= self._small_threshold_bytes:
            redis_client = self._ensure_redis()
            if redis_client is not None:
                try:
                    redis_key = self._redis_key(run_id, key)
                    redis_client.setex(
                        redis_key,
                        self._redis_ttl_seconds,
                        self._table_to_bytes(table))
                    self._record_location(
                        _ArrowLocation(
                            key=key,
                            run_id=run_id,
                            tier="redis",
                            pointer=redis_key,
                            size_bytes=size_bytes,
                        )
                    )
                    log_storage_event(
                        event_type="write_hot", tier="hot",
                        payload_bytes=size_bytes, run_id=run_id, step_name=key,
                        object_name=redis_key,
                    )
                    return "redis"
                except RedisError:
                    logger.warning(
                        "Redis unavailable for key '%s', falling back to shm", key)

        if size_bytes <= self._medium_threshold_bytes and self._shm_available:
            try:
                shm_path, _ = shm_store.write(table, run_id, key)
                self._record_location(
                    _ArrowLocation(
                        key=key,
                        run_id=run_id,
                        tier="shm",
                        pointer=str(shm_path),
                        size_bytes=size_bytes,
                    )
                )
                log_storage_event(
                    event_type="write_warm", tier="warm",
                    payload_bytes=size_bytes, run_id=run_id, step_name=key,
                    object_name=str(shm_path),
                )
                return "shm"
            except OSError:
                logger.warning("Shared-memory write failed for key '%s'", key)

        pointer = self._spill_pointer(run_id, key)
        buffer = io.BytesIO()
        pq.write_table(table, buffer, compression="zstd")
        buffer.seek(0)
        storage_service.upload(buffer, pointer)
        self._record_location(
            _ArrowLocation(
                key=key,
                run_id=run_id,
                tier="spill",
                pointer=pointer,
                size_bytes=size_bytes,
            )
        )
        log_storage_event(
            event_type="write_cold", tier="cold",
            payload_bytes=size_bytes, run_id=run_id, step_name=key,
            object_name=pointer,
        )
        return "spill"

    def store(self, key: str, table: pa.Table, *, run_id: str) -> str:
        """Compatibility wrapper for storing a table on the Arrow bus."""
        return self.put(key, table, run_id=run_id)

    def get(self, key: str) -> pa.Table:
        with self._lock:
            location = self._locations.get(key)
            if location is None:
                raise KeyError(f"Arrow bus key '{key}' not found")

            if location.tier == "redis":
                redis_client = self._ensure_redis()
                if redis_client is None:
                    raise KeyError(f"Arrow bus redis key '{key}' unavailable")
                try:
                    payload = redis_client.get(location.pointer)
                except RedisError as exc:
                    raise KeyError(
                        f"Arrow bus redis key '{key}' unavailable due to Redis error") from exc
                if payload is None:
                    raise KeyError(f"Arrow bus redis key '{key}' expired")
                payload_bytes = payload.encode(
                    "utf-8") if isinstance(payload, str) else payload
                table = self._bytes_to_table(payload_bytes)
                log_storage_event(
                    event_type="read_hot", tier="hot",
                    payload_bytes=len(payload_bytes), run_id=location.run_id,
                    step_name=key, object_name=location.pointer,
                )
                return table

            if location.tier == "shm":
                if not location.pointer:
                    raise KeyError(
                        f"Arrow bus shm key '{key}' missing pointer")
                shm_file = Path(location.pointer)
                result = shm_store.read(shm_file)
                if result is None:
                    raise KeyError(f"Arrow bus shm key '{key}' missing file")
                log_storage_event(
                    event_type="read_warm", tier="warm",
                    payload_bytes=location.size_bytes, run_id=location.run_id,
                    step_name=key, object_name=location.pointer,
                )
                return result

            if location.tier == "spill":
                with storage_service.download(location.pointer or "") as handle:
                    table = pq.read_table(handle)
                log_storage_event(
                    event_type="read_cold", tier="cold",
                    payload_bytes=location.size_bytes, run_id=location.run_id,
                    step_name=key, object_name=location.pointer,
                )
                return table

        raise KeyError(f"Arrow bus key '{key}' has unknown tier")

    def load(self, key: str) -> pa.Table:
        """Compatibility wrapper for loading a table from the Arrow bus."""
        return self.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            location = self._locations.pop(key, None)
        if location is None:
            return
        self._delete_location_payload(location)
        self._remove_manifest_entry(location.run_id, key)

    def cleanup_run(self, run_id: str) -> dict[str, int]:
        deleted = {"hot": 0, "warm": 0, "cold": 0}

        with self._lock:
            keys = [k for k, meta in self._locations.items()
                    if meta.run_id == run_id]

        for key in keys:
            loc = self._locations.get(key)
            if loc:
                if loc.tier == "redis":
                    deleted["hot"] += 1
                elif loc.tier == "shm":
                    deleted["warm"] += 1
                elif loc.tier == "spill":
                    deleted["cold"] += 1
            self.delete(key)

        for location in self._load_manifest_locations(run_id):
            if location.key in keys:
                continue
            if location.tier == "redis":
                deleted["hot"] += 1
            elif location.tier == "shm":
                deleted["warm"] += 1
            elif location.tier == "spill":
                deleted["cold"] += 1
            self._delete_location_payload(location)
            self._remove_manifest_entry(run_id, location.key)

        self._cleanup_orphaned_shm_files(run_id)
        self._cleanup_orphaned_local_spill_files(run_id)
        self._clear_manifest(run_id)

        log_storage_event(
            event_type="cleanup_run", tier="hot",
            payload_bytes=sum(deleted.values()), run_id=run_id,
        )
        return deleted

    def clear_all(self) -> None:
        with self._lock:
            for key in list(self._locations.keys()):
                self.delete(key)

    def maybe_evict_hot_to_warm(self) -> int:
        """Demote large Redis entries to /dev/shm when cache memory pressure is high.

        Called periodically (every 5 minutes by a Celery beat task).
        Evicts at most 5 entries per call to avoid stalling Redis.

        Returns the number of entries evicted.
        """
        if not self._shm_available:
            return 0

        redis_client = self._ensure_redis()
        if redis_client is None:
            return 0

        try:
            info = redis_client.info("memory")
            maxmemory = info.get("maxmemory", 0) or info.get("maxmemory_human", 0)
            if isinstance(maxmemory, str):
                return 0  # Could not determine limit
            used = info.get("used_memory", 0)
            if maxmemory <= 0 or (used / maxmemory) < REDIS_EVICT_THRESHOLD:
                return 0
        except Exception:
            return 0

        evicted = 0
        for redis_key in redis_client.scan_iter("arrow_bus:*", count=50):
            key_str = redis_key.decode() if isinstance(redis_key, bytes) else redis_key
            if key_str.startswith(("arrow_bus:manifest:", "arrow_bus:shm:")):
                continue

            try:
                raw = redis_client.get(redis_key)
            except RedisError:
                continue

            if raw is None or len(raw) < self._small_threshold_bytes // 2:
                continue

            try:
                run_id, logical_key = self._parse_redis_data_key(key_str)
            except ValueError:
                continue

            try:
                table = self._bytes_to_table(raw)
                shm_path, _ = shm_store.write(table, run_id, logical_key)
            except Exception as exc:
                logger.warning("Eviction failed for key %s: %s", logical_key, exc)
                continue

            try:
                self._record_location(_ArrowLocation(
                    key=logical_key, run_id=run_id, tier="shm",
                    pointer=str(shm_path), size_bytes=len(raw),
                ))
                redis_client.delete(redis_key)
                evicted += 1
                logger.info("Evicted %.0fKB from Redis to /dev/shm: %s",
                             len(raw) / 1024, logical_key[:30])
                log_storage_event(
                    event_type="evict_hot_to_warm", tier="warm",
                    payload_bytes=len(raw), run_id=run_id, step_name=logical_key,
                    object_name=str(shm_path),
                )
            except Exception as exc:
                logger.warning("Location update failed during eviction: %s", exc)
                shm_store.remove(shm_path)

            if evicted >= 5:
                break

        return evicted

    def get_tier_stats(self) -> dict:
        """Return utilization statistics for all three storage tiers."""
        stats: dict = {}

        # Tier 1 — Redis cache
        redis_client = self._ensure_redis()
        if redis_client is not None:
            try:
                info = redis_client.info("memory")
                used = info.get("used_memory", 0)
                maxmem = info.get("maxmemory", 0) or info.get("used_memory", 0)
                hot_key_count = sum(
                    1 for _ in redis_client.scan_iter("arrow_bus:*", count=100)
                    if not str(_).startswith(("arrow_bus:manifest:", "arrow_bus:shm:"))
                )
                stats["hot"] = {
                    "used_bytes": int(used),
                    "max_bytes":  int(maxmem) if maxmem else None,
                    "key_count":  hot_key_count,
                    "utilization": round(used / max(maxmem, 1), 4),
                }
            except Exception as exc:
                stats["hot"] = {"error": str(exc), "key_count": 0}
        else:
            stats["hot"] = {"error": "Redis unavailable", "key_count": 0}

        # Tier 2 — /dev/shm
        used_shm, total_shm = shm_store.usage_bytes()
        stats["warm"] = {
            "used_bytes":   used_shm,
            "total_bytes":  total_shm,
            "utilization":  round(used_shm / max(total_shm, 1), 4),
            "available":    self._shm_available,
        }

        # Tier 3 — spill storage
        try:
            provider = getattr(storage_service, "provider", None)
            provider_type = type(provider).__name__ if provider else "UnknownProvider"
            provider_dir = getattr(provider, "base_dir", None)
            if isinstance(provider_dir, Path) and provider_dir.is_dir():
                total_spill = sum(
                    f.stat().st_size for f in provider_dir.rglob("*.parquet")
                    if f.is_file()
                )
                spill_count = sum(1 for _ in provider_dir.rglob("*.parquet") if _.is_file())
                stats["cold"] = {
                    "object_count": spill_count,
                    "total_bytes":  total_spill,
                    "provider":     provider_type,
                }
            else:
                stats["cold"] = {"provider": provider_type, "note": "No local spill dir"}
        except Exception as exc:
            stats["cold"] = {"error": str(exc)}

        return stats

    def _parse_redis_data_key(self, key_str: str) -> tuple[str, str]:
        """Parse an Arrow bus Redis data key into (run_id, logical_key).

        Expected format: arrow_bus:{run_id}:{logical_key}
        """
        prefix = "arrow_bus:"
        if not key_str.startswith(prefix):
            raise ValueError(f"Not an arrow_bus key: {key_str}")
        rest = key_str[len(prefix):]
        parts = rest.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"Malformed arrow_bus key: {key_str}")
        return parts[0], parts[1]

    @property
    def locations(self) -> Dict[str, dict]:
        with self._lock:
            return {
                key: {
                    "run_id": meta.run_id,
                    "tier": meta.tier,
                    "pointer": meta.pointer,
                    "size_bytes": meta.size_bytes,
                }
                for key, meta in self._locations.items()
            }


_global_bus_lock = threading.Lock()
_global_bus: Optional[ArrowDataBus] = None


def get_arrow_bus() -> ArrowDataBus:
    global _global_bus
    with _global_bus_lock:
        if _global_bus is None:
            _global_bus = ArrowDataBus(
                small_threshold_mb=10.0,
                medium_threshold_mb=500.0,
                redis_ttl_seconds=3600,
                disk_prefix="arrow_bus",
            )
    return _global_bus
