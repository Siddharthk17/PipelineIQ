"""Unit tests for ArrowDataBus tiered storage."""

from __future__ import annotations

import pyarrow as pa

from backend.execution.arrow_bus import ArrowDataBus


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def setex(self, key: str, ttl: int, payload: bytes) -> None:
        self._store[key] = payload

    def get(self, key: str):
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


def _table(rows: int) -> pa.Table:
    return pa.table({"id": list(range(rows)), "value": list(range(rows))})


def test_store_small_table_in_memory() -> None:
    bus = ArrowDataBus(
        small_threshold_mb=1.0,
        medium_threshold_mb=2.0,
        redis_client=_FakeRedis(),
    )
    tier = bus.put("small", _table(10), run_id="run-1")
    assert tier == "memory"
    assert bus.locations["small"]["tier"] == "memory"


def test_store_medium_table_in_redis() -> None:
    bus = ArrowDataBus(
        small_threshold_mb=0.0001,
        medium_threshold_mb=0.01,
        redis_client=_FakeRedis(),
    )
    tier = bus.put("medium", _table(500), run_id="run-1")
    assert tier == "redis"
    assert bus.locations["medium"]["tier"] == "redis"


def test_store_large_table_on_disk() -> None:
    bus = ArrowDataBus(
        small_threshold_mb=0.0001,
        medium_threshold_mb=0.0002,
        redis_client=_FakeRedis(),
    )
    tier = bus.put("large", _table(10_000), run_id="run-1")
    assert tier == "disk"
    assert bus.locations["large"]["tier"] == "disk"
    bus.delete("large")


def test_retrieve_from_all_tiers() -> None:
    bus = ArrowDataBus(
        small_threshold_mb=0.001,
        medium_threshold_mb=0.01,
        redis_client=_FakeRedis(),
    )
    expected = _table(100)
    bus.put("k1", expected, run_id="run-1")
    actual = bus.get("k1")
    assert actual.equals(expected)


def test_cleanup_run_data_removes_only_matching_run_keys() -> None:
    bus = ArrowDataBus(
        small_threshold_mb=1.0,
        medium_threshold_mb=2.0,
        redis_client=_FakeRedis(),
    )
    bus.put("run1_a", _table(10), run_id="run-1")
    bus.put("run1_b", _table(10), run_id="run-1")
    bus.put("run2_a", _table(10), run_id="run-2")

    bus.cleanup_run("run-1")

    assert "run1_a" not in bus.locations
    assert "run1_b" not in bus.locations
    assert "run2_a" in bus.locations

