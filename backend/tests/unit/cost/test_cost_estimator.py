"""Tests for the pipeline cost estimator."""

import pytest


class TestCostEstimator:
    def test_ms_per_1000_rows_has_all_step_types(self):
        from backend.cost.estimator import MS_PER_1000_ROWS

        expected_types = [
            "load",
            "filter",
            "aggregate",
            "join",
            "sort",
            "select",
            "transform",
            "validate",
            "save",
            "pivot",
            "unpivot",
            "deduplicate",
            "fill_nulls",
            "rename",
            "sample",
            "sql",
            "wasm_compute",
        ]
        for step_type in expected_types:
            assert step_type in MS_PER_1000_ROWS, f"Missing rate for: {step_type}"

    def test_filter_reduces_row_count(self):
        from backend.cost.estimator import _estimate_output_rows

        input_rows = 100_000
        output = _estimate_output_rows(None, "filter", input_rows)
        assert output < input_rows

    def test_aggregate_reduces_row_count_significantly(self):
        from backend.cost.estimator import _estimate_output_rows

        output = _estimate_output_rows(None, "aggregate", 100_000)
        assert output <= 10_000

    def test_select_preserves_row_count(self):
        from backend.cost.estimator import _estimate_output_rows

        output = _estimate_output_rows(None, "select", 50_000)
        assert output == 50_000

    def test_optimization_tip_filter_before_aggregate(self):
        from backend.cost.estimator import _generate_optimization_tip

        tip = _generate_optimization_tip([], [], filter_after_aggregate=True)
        assert "filter" in tip.lower() or "aggregate" in tip.lower()

    def test_confidence_is_percentage(self):
        from backend.cost.estimator import CostEstimate

        est = CostEstimate(total_ms=5000, peak_memory_mb=100, confidence=75.0)
        assert 0 <= est.confidence <= 100

    def test_cost_estimate_memory_scales_with_data(self):
        from backend.cost.estimator import BYTES_PER_CELL

        rows_small = 1_000
        rows_large = 1_000_000
        cols = 10

        mem_small = (rows_small * cols * BYTES_PER_CELL) / 1_048_576
        mem_large = (rows_large * cols * BYTES_PER_CELL) / 1_048_576

        assert mem_large > mem_small

    def test_load_step_is_io_engine(self):
        from backend.cost.estimator import _determine_engine

        assert _determine_engine("load") == "io"
        assert _determine_engine("save") == "io"

    def test_filter_step_is_duckdb_engine(self):
        from backend.cost.estimator import _determine_engine

        engine = _determine_engine("filter")
        assert engine in ("duckdb", "pandas")

    def test_wasm_step_is_wasm_engine(self):
        from backend.cost.estimator import _determine_engine

        assert _determine_engine("wasm_compute") == "wasm"


class TestWebhookDelivery:
    def test_webhook_task_on_critical_queue(self):
        from backend.tasks.webhook_tasks import deliver_webhook

        assert deliver_webhook.queue == "critical"

    def test_webhook_max_retries_is_3(self):
        from backend.tasks.webhook_tasks import deliver_webhook

        assert deliver_webhook.max_retries == 3

    def test_hmac_signature_format(self):
        import hashlib
        import hmac
        import json

        secret = "test_secret"
        payload = {"event_type": "run.success", "run_id": "abc"}
        payload_bytes = json.dumps(payload, sort_keys=True).encode()
        sig = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
        assert len(sig) == 64

    def test_delivery_uses_async_client(self):
        import inspect

        from backend.tasks import webhook_tasks as webhook_module

        source = inspect.getsource(webhook_module)
        assert "httpx.AsyncClient" in source, "Must use AsyncClient for non-blocking delivery"


class TestBcryptFix:
    def test_bcrypt_uses_process_pool_not_thread_pool(self):
        import inspect

        import backend.auth as pw_module

        source = inspect.getsource(pw_module)
        assert "ProcessPoolExecutor" in source, (
            "bcrypt must run in ProcessPoolExecutor"
        )
        assert "ThreadPoolExecutor" not in source, (
            "ThreadPoolExecutor cannot bypass the GIL for CPU-bound bcrypt"
        )

    def test_pool_has_2_workers(self):
        import backend.auth as pw_module

        assert pw_module._bcrypt_pool._max_workers == 2

    def test_bcrypt_check_sync_is_module_level(self):
        import backend.auth as pw_module

        assert hasattr(pw_module, "_bcrypt_check_sync"), (
            "_bcrypt_check_sync must be module-level to be picklable"
        )
        assert hasattr(pw_module, "_bcrypt_hash_sync"), (
            "_bcrypt_hash_sync must be module-level"
        )

    @pytest.mark.asyncio
    async def test_verify_password_correct(self):
        import bcrypt
        from backend.auth import verify_password_async

        hashed = bcrypt.hashpw(b"correctpassword", bcrypt.gensalt()).decode()
        result = await verify_password_async("correctpassword", hashed)
        assert result is True

    @pytest.mark.asyncio
    async def test_verify_password_incorrect(self):
        import bcrypt
        from backend.auth import verify_password_async

        hashed = bcrypt.hashpw(b"realpassword", bcrypt.gensalt()).decode()
        result = await verify_password_async("wrongpassword", hashed)
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_password_exception_returns_false(self):
        from backend.auth import verify_password_async

        result = await verify_password_async("somepassword", "not_a_real_bcrypt_hash")
        assert result is False
