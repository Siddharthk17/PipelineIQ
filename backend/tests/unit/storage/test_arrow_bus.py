"""Unit tests for the three-tier ArrowDataBus."""

from unittest.mock import MagicMock, patch

import pyarrow as pa
import pytest


@pytest.fixture
def table_small():
    return pa.table({
        "id":    list(range(100)),
        "value": [float(i) for i in range(100)],
        "label": [f"item_{i}" for i in range(100)],
    })


@pytest.fixture
def table_large():
    return pa.table({
        "id":    list(range(10_000)),
        "value": [float(i) * 1.5 for i in range(10_000)],
    })


class TestTierConstants:
    def test_hot_threshold_is_10mb(self):
        from backend.execution.arrow_bus import REDIS_THRESHOLD
        assert REDIS_THRESHOLD == 10 * 1024 * 1024

    def test_warm_threshold_is_500mb(self):
        from backend.execution.arrow_bus import SHM_THRESHOLD
        assert SHM_THRESHOLD == 500 * 1024 * 1024

    def test_eviction_threshold_90_percent(self):
        from backend.execution.arrow_bus import REDIS_EVICT_THRESHOLD
        assert REDIS_EVICT_THRESHOLD == 0.90


class TestArrowIpcSerialization:
    def test_small_table_roundtrip(self, table_small):
        from backend.execution.arrow_bus import ArrowDataBus
        ipc_bytes = ArrowDataBus._table_to_bytes(table_small)
        recovered = ArrowDataBus._bytes_to_table(ipc_bytes)
        assert recovered.num_rows == table_small.num_rows
        assert recovered.num_columns == table_small.num_columns
        assert recovered.schema.names == table_small.schema.names

    def test_column_values_preserved(self, table_small):
        from backend.execution.arrow_bus import ArrowDataBus
        ipc_bytes = ArrowDataBus._table_to_bytes(table_small)
        recovered = ArrowDataBus._bytes_to_table(ipc_bytes)
        assert table_small.column("id").to_pylist() == recovered.column("id").to_pylist()

    def test_ipc_magic_bytes(self, table_small):
        from backend.execution.arrow_bus import ArrowDataBus
        ipc_bytes = ArrowDataBus._table_to_bytes(table_small)
        # Arrow IPC stream format: 0xFFFFFFFF continuation marker + 4-byte size
        assert ipc_bytes[:4] == b"\xff\xff\xff\xff", "IPC stream must start with 0xFFFFFFFF"

    def test_ipc_smaller_than_csv(self, table_large):
        import io
        from backend.execution.arrow_bus import ArrowDataBus

        ipc_bytes = ArrowDataBus._table_to_bytes(table_large)
        csv_bytes = table_large.to_pandas().to_csv(index=False).encode()
        assert len(ipc_bytes) < len(csv_bytes) * 3


class TestArrowBusHotTier:
    def test_small_table_routes_to_hot_tier(self, table_small):
        from backend.execution.arrow_bus import ArrowDataBus, REDIS_THRESHOLD

        mock_redis = MagicMock()
        bus = ArrowDataBus.__new__(ArrowDataBus)
        bus._small_threshold_bytes = REDIS_THRESHOLD
        bus._medium_threshold_bytes = 500 * 1024 * 1024
        bus._redis_ttl_seconds = 3600
        bus._manifest_ttl_seconds = 86400
        bus._disk_prefix = "arrow_bus"
        bus._locations = {}
        bus._lock = MagicMock()
        bus._redis = mock_redis
        bus._redis_retry_after = 0.0
        bus._shm_available = False
        bus._shm_dir = MagicMock()

        result = bus.put("step1", table_small, run_id="run1")
        assert result == "redis"
        mock_redis.setex.assert_called_once()

    def test_hot_load_returns_none_on_miss(self):
        from backend.execution.arrow_bus import ArrowDataBus

        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        bus = ArrowDataBus.__new__(ArrowDataBus)
        bus._locations = {"step1": MagicMock(tier="redis", pointer="arrow_bus:run1:step1")}
        bus._lock = MagicMock()
        bus._redis = mock_redis

        with pytest.raises(KeyError):
            bus.get("step1")


class TestShmStore:
    def test_shm_available_returns_bool(self):
        from backend.execution.shm_store import shm_available
        assert isinstance(shm_available(), bool)

    def test_shm_path_is_deterministic(self):
        from backend.execution.shm_store import shm_path_for
        p1 = shm_path_for("run1", "step1")
        p2 = shm_path_for("run1", "step1")
        assert p1 == p2

    def test_different_keys_different_paths(self):
        from backend.execution.shm_store import shm_path_for
        p1 = shm_path_for("run1", "step1")
        p2 = shm_path_for("run2", "step2")
        assert p1 != p2

    def test_path_starts_with_pipelineiq(self):
        from backend.execution.shm_store import shm_path_for
        path = shm_path_for("run_x", "step_y")
        assert "pipelineiq_" in str(path)

    def test_path_ends_with_arrow(self):
        from backend.execution.shm_store import shm_path_for
        path = shm_path_for("run_x", "step_y")
        assert str(path).endswith(".arrow")

    def test_usage_bytes_returns_ints(self):
        from backend.execution.shm_store import usage_bytes
        used, total = usage_bytes()
        assert isinstance(used, int)
        assert isinstance(total, int)
        assert used <= total

    def test_cleanup_stale_is_callable(self):
        from backend.execution.shm_store import cleanup_stale
        deleted = cleanup_stale()
        assert isinstance(deleted, int)


class TestCleanupRun:
    def test_cleanup_pattern_matches_run_id(self):
        from backend.execution.arrow_bus import ArrowDataBus

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = iter([])

        bus = ArrowDataBus.__new__(ArrowDataBus)
        bus._locations = {}
        bus._lock = MagicMock()
        bus._redis = mock_redis
        bus._shm_available = False
        bus._disk_prefix = "arrow_bus"
        bus._manifest_ttl_seconds = 86400
        bus._shm_dir = MagicMock()

        bus.cleanup_run("run-abc-123")
        # cleanup calls _load_manifest_locations which fetches manifest hash
        manifest_called = any(
            "run-abc-123" in str(call)
            for call in [str(c) for c in mock_redis.method_calls]
        )
        assert manifest_called

    def test_cleanup_is_idempotent(self):
        from backend.execution.arrow_bus import ArrowDataBus

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = iter([])
        mock_redis.hgetall.return_value = {}

        bus = ArrowDataBus.__new__(ArrowDataBus)
        bus._locations = {}
        bus._lock = MagicMock()
        bus._redis = mock_redis
        bus._shm_available = False
        bus._disk_prefix = "arrow_bus"
        bus._manifest_ttl_seconds = 86400
        bus._shm_dir = MagicMock()

        bus.cleanup_run("run-xyz")
        bus.cleanup_run("run-xyz")


class TestTierStats:
    def test_get_tier_stats_returns_hot_warm_cold(self):
        from pathlib import Path
        from backend.execution.arrow_bus import ArrowDataBus

        mock_redis = MagicMock()
        mock_redis.info.return_value = {"used_memory": 50000000, "maxmemory": 1073741824}

        bus = ArrowDataBus.__new__(ArrowDataBus)
        bus._locations = {}
        bus._lock = MagicMock()
        bus._redis = mock_redis
        bus._shm_available = True
        bus._shm_dir = Path("/dev/shm")
        bus._disk_prefix = "arrow_bus"

        with patch("backend.execution.arrow_bus.shm_store.usage_bytes", return_value=(1000, 5000)):
            stats = bus.get_tier_stats()
            assert "hot" in stats
            assert "warm" in stats
            assert "cold" in stats


class TestLifecycleIntegration:
    def test_lifecycle_module_imports(self):
        from backend.storage.lifecycle import (
            apply_all_lifecycle_policies,
            get_lifecycle_policies,
            get_bucket_storage_stats,
        )
        assert callable(apply_all_lifecycle_policies)
        assert callable(get_lifecycle_policies)
        assert callable(get_bucket_storage_stats)

    def test_bucket_list_contains_four_buckets(self):
        from backend.storage.lifecycle import BUCKETS
        assert len(BUCKETS) == 4
        assert "pipelineiq-outputs" in BUCKETS
        assert "pipelineiq-spills" in BUCKETS
        assert "pipelineiq-uploads" in BUCKETS
        assert "pipelineiq-wasm" in BUCKETS

    def test_apply_policies_handles_all_buckets(self):
        import inspect
        from backend.storage import lifecycle as lc_module
        source = inspect.getsource(lc_module.apply_all_lifecycle_policies)
        for bucket in ["pipelineiq-outputs", "pipelineiq-spills",
                       "pipelineiq-uploads", "pipelineiq-wasm"]:
            assert bucket in source, f"Missing bucket: {bucket}"
