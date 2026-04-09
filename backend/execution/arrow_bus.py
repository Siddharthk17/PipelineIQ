"""Arrow table bus with tiered storage for large intermediate results."""

from __future__ import annotations

import base64
import io
import logging
import threading
from dataclasses import dataclass
from typing import Dict, Optional

import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
from redis.exceptions import RedisError

from backend.config import settings
from backend.db.redis_pools import get_cache_redis
from backend.services.storage_service import storage_service

logger = logging.getLogger(__name__)


@dataclass
class _ArrowLocation:
    key: str
    run_id: str
    tier: str
    pointer: Optional[str] = None
    size_bytes: int = 0


class ArrowDataBus:
    """Store Arrow tables in memory, Redis, or disk depending on size."""

    def __init__(
        self,
        *,
        small_threshold_mb: float = 50.0,
        medium_threshold_mb: float = 500.0,
        redis_ttl_seconds: int = 3600,
        redis_client=None,
        disk_prefix: str = "arrow_bus",
    ) -> None:
        if small_threshold_mb <= 0:
            raise ValueError("small_threshold_mb must be > 0")
        if medium_threshold_mb <= small_threshold_mb:
            raise ValueError("medium_threshold_mb must be > small_threshold_mb")

        self._small_threshold_bytes = int(small_threshold_mb * 1024 * 1024)
        self._medium_threshold_bytes = int(medium_threshold_mb * 1024 * 1024)
        self._redis_ttl_seconds = redis_ttl_seconds
        self._disk_prefix = disk_prefix.strip("/")
        self._memory_store: Dict[str, pa.Table] = {}
        self._locations: Dict[str, _ArrowLocation] = {}
        self._lock = threading.Lock()
        self._redis = redis_client

    def _ensure_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            self._redis = get_cache_redis()
        except Exception:
            self._redis = None
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

    def put(self, key: str, table: pa.Table, *, run_id: str) -> str:
        """Store a table and return selected storage tier."""
        if not key:
            raise ValueError("Arrow bus key cannot be empty")
        if not isinstance(table, pa.Table):
            raise ValueError("Arrow bus accepts only pyarrow.Table inputs")

        size_bytes = self._estimated_size(table)
        with self._lock:
            self.delete(key)

            if size_bytes <= self._small_threshold_bytes:
                self._memory_store[key] = table
                self._locations[key] = _ArrowLocation(
                    key=key,
                    run_id=run_id,
                    tier="memory",
                    size_bytes=size_bytes,
                )
                return "memory"

            redis_client = self._ensure_redis()
            if size_bytes <= self._medium_threshold_bytes and redis_client is not None:
                redis_key = f"arrow_bus:{run_id}:{key}"
                payload = base64.b64encode(self._table_to_bytes(table)).decode("ascii")
                redis_client.setex(redis_key, self._redis_ttl_seconds, payload)
                self._locations[key] = _ArrowLocation(
                    key=key,
                    run_id=run_id,
                    tier="redis",
                    pointer=redis_key,
                    size_bytes=size_bytes,
                )
                return "redis"

            pointer = f"{self._disk_prefix}/{run_id}/{key}.parquet"
            buffer = io.BytesIO()
            pq.write_table(table, buffer, compression="zstd")
            buffer.seek(0)
            storage_service.upload(buffer, pointer)
            self._locations[key] = _ArrowLocation(
                key=key,
                run_id=run_id,
                tier="disk",
                pointer=pointer,
                size_bytes=size_bytes,
            )
            return "disk"

    def get(self, key: str) -> pa.Table:
        with self._lock:
            location = self._locations.get(key)
            if location is None:
                raise KeyError(f"Arrow bus key '{key}' not found")

            if location.tier == "memory":
                return self._memory_store[key]

            if location.tier == "redis":
                redis_client = self._ensure_redis()
                if redis_client is None:
                    raise KeyError(f"Arrow bus redis key '{key}' unavailable")
                payload = redis_client.get(location.pointer)
                if payload is None:
                    raise KeyError(f"Arrow bus redis key '{key}' expired")
                if isinstance(payload, str):
                    payload_bytes = base64.b64decode(payload.encode("ascii"))
                else:
                    payload_bytes = base64.b64decode(payload)
                return self._bytes_to_table(payload_bytes)

            if location.tier == "disk":
                with storage_service.download(location.pointer or "") as handle:
                    return pq.read_table(handle)

        raise KeyError(f"Arrow bus key '{key}' has unknown tier")

    def delete(self, key: str) -> None:
        location = self._locations.pop(key, None)
        self._memory_store.pop(key, None)
        if location is None:
            return

        if location.tier == "redis" and location.pointer:
            redis_client = self._ensure_redis()
            if redis_client is not None:
                try:
                    redis_client.delete(location.pointer)
                except RedisError:
                    logger.warning("Failed to delete Arrow bus redis key %s", location.pointer)
        elif location.tier == "disk" and location.pointer:
            try:
                storage_service.delete(location.pointer)
            except Exception:
                logger.warning("Failed to delete Arrow bus disk key %s", location.pointer)

    def cleanup_run(self, run_id: str) -> None:
        with self._lock:
            keys = [k for k, meta in self._locations.items() if meta.run_id == run_id]
            for key in keys:
                self.delete(key)

    def clear_all(self) -> None:
        with self._lock:
            for key in list(self._locations.keys()):
                self.delete(key)

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
                small_threshold_mb=50.0,
                medium_threshold_mb=500.0,
                redis_ttl_seconds=3600,
                disk_prefix="arrow_bus",
            )
    return _global_bus
