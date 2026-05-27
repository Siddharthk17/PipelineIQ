"""Unit tests for the WasmExecutor.

Tests correctness, caching, error handling, and fuel budget enforcement.
All tests use real Wasm modules compiled from WAT fixtures.
"""

import time

import pyarrow as pa
import pytest

from backend.execution.wasm_executor import (
    FUEL_PER_ROW,
    FUEL_PER_STEP,
    WasmExecutor,
)


class TestWasmExecutorCorrectness:
    def test_simple_add_function(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0, 3.0], "b": [10.0, 20.0, 30.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)

        assert "sum" in result.schema.names
        sums = result.column("sum").to_pylist()
        assert sums[0] == pytest.approx(11.0)
        assert sums[1] == pytest.approx(22.0)
        assert sums[2] == pytest.approx(33.0)

    def test_risk_score_function(self, risk_score_wasm):
        table = pa.table({
            "age": [25.0, 40.0, 55.0],
            "income": [50000.0, 80000.0, 120000.0],
            "credit_score": [700.0, 750.0, 800.0],
            "payment_history": [90.0, 95.0, 99.0],
        })

        class MockStep:
            function = "compute_risk"
            input_columns = ["age", "income", "credit_score", "payment_history"]
            output_column = "risk_score"

        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), risk_score_wasm)

        assert "risk_score" in result.schema.names
        scores = result.column("risk_score").to_pylist()
        assert all(0.0 <= s <= 1.0 for s in scores if s is not None)
        assert scores[2] > scores[0]

    def test_output_column_added_preserving_existing(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0], "b": [3.0, 4.0], "existing": [99.0, 88.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "result"

        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)

        assert "a" in result.schema.names
        assert "b" in result.schema.names
        assert "existing" in result.schema.names
        assert "result" in result.schema.names
        assert result.num_rows == 2

    def test_null_on_missing_input_column(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="not found in data"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_missing_function_raises_value_error(self, simple_add_wasm):
        table = pa.table({"a": [1.0], "b": [2.0]})

        class MockStep:
            function = "nonexistent_function"
            input_columns = ["a", "b"]
            output_column = "result"

        executor = WasmExecutor()
        with pytest.raises(ValueError, match="not found"):
            executor.execute(table, MockStep(), simple_add_wasm)

    def test_row_count_preserved(self, simple_add_wasm):
        table = pa.table({"a": list(range(1000)), "b": list(range(1000))})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)
        assert result.num_rows == 1000


class TestWasmModuleCaching:
    def test_module_cached_after_first_compile(self, simple_add_wasm):
        executor = WasmExecutor()
        table = pa.table({"a": [1.0], "b": [2.0]})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        assert executor.cached_module_count == 0
        executor.execute(table, MockStep(), simple_add_wasm)
        assert executor.cached_module_count == 1
        executor.execute(table, MockStep(), simple_add_wasm)
        assert executor.cached_module_count == 1

    def test_different_modules_cached_separately(self, simple_add_wasm, multi_export_wasm):
        executor = WasmExecutor()
        table = pa.table({"a": [1.0], "b": [2.0]})

        class AddStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        class DoubleStep:
            function = "double"
            input_columns = ["a"]
            output_column = "doubled"

        executor.execute(table, AddStep(), simple_add_wasm)
        executor.execute(table, DoubleStep(), multi_export_wasm)
        assert executor.cached_module_count == 2


class TestWasmErrorHandling:
    def test_invalid_wasm_bytes_raise_on_validate(self, invalid_wasm_bytes):
        executor = WasmExecutor()
        result = executor.validate(invalid_wasm_bytes)
        assert result.valid is False
        assert result.error is not None
        assert len(result.error) > 0

    def test_missing_function_name_detected_in_validate(self, simple_add_wasm):
        executor = WasmExecutor()
        result = executor.validate(simple_add_wasm, function_name="nonexistent")
        assert result.valid is False
        assert "nonexistent" in result.error

    def test_valid_module_passes_validation(self, simple_add_wasm):
        executor = WasmExecutor()
        result = executor.validate(simple_add_wasm, function_name="add")
        assert result.valid is True
        assert "add" in result.exported_functions

    def test_string_column_produces_null_not_crash(self, simple_add_wasm):
        table = pa.table({"label": ["cat", "dog", "bird"], "val": [1.0, 2.0, 3.0]})

        class MockStep:
            function = "add"
            input_columns = ["label"]
            output_column = "result"

        executor = WasmExecutor()
        result = executor.execute(table, MockStep(), simple_add_wasm)
        values = result.column("result").to_pylist()
        assert all(v is None for v in values)


class TestWasmFuelBudget:
    def test_infinite_loop_does_not_hang(self, infinite_loop_wasm):
        table = pa.table({"x": [1.0, 2.0, 3.0]})

        class MockStep:
            function = "infinite"
            input_columns = ["x"]
            output_column = "result"

        executor = WasmExecutor()
        start = time.time()
        result = executor.execute(table, MockStep(), infinite_loop_wasm)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"Infinite loop was not killed by fuel budget (took {elapsed:.1f}s)"
        values = result.column("result").to_pylist()
        assert all(v is None for v in values), f"Expected all None, got: {values}"

    def test_fuel_constants_are_sensible(self):
        assert FUEL_PER_ROW == 1_000
        assert FUEL_PER_STEP == 10_000_000


class TestWasmMultipleExports:
    def test_can_call_any_exported_function(self, multi_export_wasm):
        table = pa.table({"x": [4.0, 9.0, 16.0]})
        executor = WasmExecutor()

        for fn_name, expected in [("double", [8.0, 18.0, 32.0]),
                                    ("square", [16.0, 81.0, 256.0])]:
            class MockStep:
                function = fn_name
                input_columns = ["x"]
                output_column = "result"

            result = executor.execute(table, MockStep(), multi_export_wasm)
            values = result.column("result").to_pylist()
            for got, exp in zip(values, expected):
                assert got == pytest.approx(exp), f"{fn_name}: expected {exp}, got {got}"

    def test_validation_lists_all_exports(self, multi_export_wasm):
        executor = WasmExecutor()
        result = executor.validate(multi_export_wasm)
        assert set(result.exported_functions) >= {"double", "square", "negate"}
