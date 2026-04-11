"""Unit tests for ArrowDataBus tiered storage."""

from __future__ import annotations

import io
from pathlib import Path

import pyarrow as pa

from backend.execution.arrow_bus import ArrowDataBus


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._hash_store: dict[str, dict[str, str]] = {}

    def setex(self, key: str, ttl: int, payload: bytes) -> None:
        self._store[key] = payload

    def get(self, key: str):
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._hash_store.pop(key, None)

    def hset(self, key: str, field: str, value: str) -> None:
        bucket = self._hash_store.setdefault(key, {})
        bucket[field] = value

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hash_store.get(key, {}))

    def hdel(self, key: str, field: str) -> None:
        bucket = self._hash_store.get(key)
        if bucket is None:
            return
        bucket.pop(field, None)
        if not bucket:
            self._hash_store.pop(key, None)

    def hlen(self, key: str) -> int:
        return len(self._hash_store.get(key, {}))

    def expire(self, key: str, ttl: int) -> None:
        # Fake in-memory store does not enforce TTL.
        return None


class _FakeStorage:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def upload(self, file_obj, destination_path: str) -> str:
        self._store[destination_path] = file_obj.read()
        return destination_path

    def download(self, path: str):
        return io.BytesIO(self._store[path])

    def delete(self, path: str) -> None:
        self._store.pop(path, None)


def _table(rows: int) -> pa.Table:
    return pa.table({"id": list(range(rows)), "value": list(range(rows))})


def test_store_small_table_in_redis(tmp_path) -> None:
    bus = ArrowDataBus(
        small_threshold_mb=1.0,
        medium_threshold_mb=2.0,
        redis_client=_FakeRedis(),
        shm_dir=str(tmp_path),
    )
    tier = bus.put("small", _table(10), run_id="run-1")
    assert tier == "redis"
    assert bus.locations["small"]["tier"] == "redis"


def test_store_medium_table_in_shm(tmp_path) -> None:
    bus = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.1,
        redis_client=_FakeRedis(),
        shm_dir=str(tmp_path),
    )
    tier = bus.put("medium", _table(5_000), run_id="run-1")
    assert tier == "shm"
    assert bus.locations["medium"]["tier"] == "shm"
    assert bus.get("medium").num_rows == 5_000


def test_store_large_table_in_spill(monkeypatch, tmp_path) -> None:
    fake_storage = _FakeStorage()
    monkeypatch.setattr("backend.execution.arrow_bus.storage_service", fake_storage)
    bus = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.00002,
        redis_client=_FakeRedis(),
        shm_dir=str(tmp_path),
    )
    tier = bus.put("large", _table(10_000), run_id="run-1")
    assert tier == "spill"
    assert bus.locations["large"]["tier"] == "spill"
    assert bus.get("large").num_rows == 10_000


def test_cleanup_run_data_removes_only_matching_run_keys(tmp_path) -> None:
    bus = ArrowDataBus(
        small_threshold_mb=1.0,
        medium_threshold_mb=2.0,
        redis_client=_FakeRedis(),
        shm_dir=str(tmp_path),
    )
    bus.put("run1_a", _table(10), run_id="run-1")
    bus.put("run1_b", _table(10), run_id="run-1")
    bus.put("run2_a", _table(10), run_id="run-2")

    bus.cleanup_run("run-1")

    assert "run1_a" not in bus.locations
    assert "run1_b" not in bus.locations
    assert "run2_a" in bus.locations


def test_cleanup_run_recovers_spill_from_manifest_after_restart(
    monkeypatch, tmp_path
) -> None:
    fake_storage = _FakeStorage()
    fake_redis = _FakeRedis()
    monkeypatch.setattr("backend.execution.arrow_bus.storage_service", fake_storage)

    bus_one = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.00002,
        redis_client=fake_redis,
        shm_dir=str(tmp_path),
    )
    tier = bus_one.put("large", _table(10_000), run_id="run-1")
    assert tier == "spill"
    spill_pointer = bus_one.locations["large"]["pointer"]
    assert spill_pointer in fake_storage._store

    # Simulate process restart: new bus has no in-memory locations.
    bus_two = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.00002,
        redis_client=fake_redis,
        shm_dir=str(tmp_path),
    )
    assert bus_two.locations == {}

    bus_two.cleanup_run("run-1")

    assert spill_pointer not in fake_storage._store


def test_cleanup_run_removes_orphaned_shm_files_by_run_pattern(tmp_path) -> None:
    fake_redis = _FakeRedis()
    bus_one = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.1,
        redis_client=fake_redis,
        shm_dir=str(tmp_path),
    )
    tier = bus_one.put("medium", _table(5_000), run_id="run-1")
    assert tier == "shm"
    shm_pointer = bus_one.locations["medium"]["pointer"]
    assert shm_pointer is not None

    # Simulate crash + restart with lost in-memory and lost manifest.
    fake_redis.delete("arrow_bus:manifest:run-1")
    bus_two = ArrowDataBus(
        small_threshold_mb=0.00001,
        medium_threshold_mb=0.1,
        redis_client=fake_redis,
        shm_dir=str(tmp_path),
    )
    bus_two.cleanup_run("run-1")

    assert not Path(shm_pointer).exists()
