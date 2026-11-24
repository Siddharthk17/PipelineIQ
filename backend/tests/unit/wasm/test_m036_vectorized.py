"""Regression tests for M-036: vectorized batch execution of Wasm UDFs.

Bug: the executor called the Wasm function once per row from Python
(~20us of FFI overhead per call, ~46k rows/s), so a 550k-row table took
~25s. The fix batches rows through a generated in-Wasm loop driver
(one FFI call per ~4k-row chunk) with per-row fallback on traps.

These tests pin the fix: wall-clock speedup on a large table, exact
output parity, and preservation of M-012 trap isolation plus M-130 fuel
semantics through the batch path.
"""

import time

import pyarrow as pa
from wasmtime import wat2wasm

from backend.execution.wasm_executor import FUEL_PER_ROW, WasmExecutor

# UDF that traps only on negative inputs: exercises chunk-level fallback
# (a chunk containing one negative must still isolate per-row Nones).
COND_TRAP_WASM = bytes(
    wat2wasm("""
        (module
            (func (export "pos") (param f64) (result f64)
                (if (result f64)
                    (f64.lt (local.get 0) (f64.const 0))
                    (then unreachable)
                    (else (local.get 0)))))
    """)
)

# Non-f64 export: the batch driver cannot bind to it, so execution must
# fall back to the legacy per-row path (None per row, no crash).
I32_WASM = bytes(
    wat2wasm("""
        (module
            (func (export "int_func") (param i32) (result i32)
                local.get 0))
    """)
)


class TestVectorizedSpeedup:
    def test_300k_rows_under_5s_with_exact_parity(self, simple_add_wasm):
        """Order-of-magnitude regression test: pre-fix this takes ~6.5s."""
        n = 300_000
        table = pa.table(
            {
                "a": [float(i) for i in range(n)],
                "b": [1.0] * n,
            }
        )

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        executor = WasmExecutor()
        start = time.time()
        result = executor.execute(table, MockStep(), simple_add_wasm)
        elapsed = time.time() - start

        assert elapsed < 5.0, f"300k-row UDF took {elapsed:.2f}s — batch path lost?"
        assert result.num_rows == n
        got = result.column("sum").to_pylist()
        assert got == [float(i) + 1.0 for i in range(n)]

    def test_multi_column_parity(self, risk_score_wasm):
        table = pa.table(
            {
                "age": [25.0, 40.0, 55.0, 30.0],
                "income": [50000.0, 80000.0, 120000.0, 60000.0],
                "credit_score": [700.0, 750.0, 800.0, 650.0],
                "payment_history": [90.0, 95.0, 99.0, 80.0],
            }
        )

        class MockStep:
            function = "compute_risk"
            input_columns = ["age", "income", "credit_score", "payment_history"]
            output_column = "risk_score"

        result = WasmExecutor().execute(table, MockStep(), risk_score_wasm)
        scores = result.column("risk_score").to_pylist()
        assert len(scores) == 4
        assert all(s is not None and 0.0 <= s <= 1.0 for s in scores)
        assert scores[2] > scores[0]


class TestBatchTrapIsolation:
    def test_all_traps_null_only_output(self):
        table = pa.table({"x": [1.0, 2.0, 3.0, 4.0, 5.0]})

        class BoomStep:
            function = "boom"
            input_columns = ["x"]
            output_column = "y"

        boom = bytes(
            wat2wasm('(module (func (export "boom") (param f64) (result f64) unreachable))')
        )
        result = WasmExecutor().execute(table, BoomStep(), boom)
        assert result.num_rows == 5
        assert result.column("x").to_pylist() == [1.0, 2.0, 3.0, 4.0, 5.0]
        assert result.column("y").to_pylist() == [None] * 5

    def test_partial_traps_isolate_per_row(self):
        """Chunks containing a trap fall back to per-row isolation."""
        # 5000 rows (spans two 4096-row chunks) with a sparse trap every
        # 100th row: 50 traps, well under the too-many-errors breaker.
        xs = [float(i) if i % 100 else -1.0 for i in range(5000)]
        table = pa.table({"x": xs})

        class PosStep:
            function = "pos"
            input_columns = ["x"]
            output_column = "y"

        result = WasmExecutor().execute(table, PosStep(), COND_TRAP_WASM)
        assert result.num_rows == 5000
        assert result.column("x").to_pylist() == xs
        expected = [None if v < 0 else v for v in xs]
        assert result.column("y").to_pylist() == expected

    def test_string_column_still_null(self, simple_add_wasm):
        table = pa.table({"label": ["cat", "dog"] * 500, "val": [1.0] * 1000})

        class MockStep:
            function = "add"
            input_columns = ["label"]
            output_column = "result"

        result = WasmExecutor().execute(table, MockStep(), simple_add_wasm)
        assert result.column("result").to_pylist() == [None] * 1000

    def test_non_f64_export_falls_back_to_slow_path(self):
        table = pa.table({"x": [1.0, 2.0, 3.0]})

        class IntStep:
            function = "int_func"
            input_columns = ["x"]
            output_column = "y"

        result = WasmExecutor().execute(table, IntStep(), I32_WASM)
        assert result.column("y").to_pylist() == [None, None, None]


class TestBatchFuelSemantics:
    def test_heavy_udf_runs_via_batch(self):
        heavy = bytes(
            wat2wasm("""
                (module
                  (func (export "heavy") (param f64) (result f64)
                    (local $i i32) (local $acc f64)
                    (local.set $i (i32.const 0))
                    (local.set $acc (f64.const 0))
                    (block $exit
                      (loop $l
                        (br_if $exit (i32.ge_u (local.get $i) (i32.const 5000)))
                        (local.set $acc (f64.add (local.get $acc) (local.get 0)))
                        (local.set $i (i32.add (local.get $i) (i32.const 1)))
                        (br $l)))
                    (local.get $acc)))
            """)
        )

        class HeavyStep:
            function = "heavy"
            input_columns = ["x"]
            output_column = "y"

        table = pa.table({"x": [1.0, 2.0, 3.0]})
        result = WasmExecutor().execute(table, HeavyStep(), heavy)
        assert result.column("y").to_pylist() == [5000.0, 10000.0, 15000.0]

    def test_tiny_budget_still_traps_to_none(self):
        heavy = bytes(
            wat2wasm("""
                (module
                  (func (export "heavy") (param f64) (result f64)
                    (local $i i32) (local $acc f64)
                    (local.set $i (i32.const 0))
                    (local.set $acc (f64.const 0))
                    (block $exit
                      (loop $l
                        (br_if $exit (i32.ge_u (local.get $i) (i32.const 5000)))
                        (local.set $acc (f64.add (local.get $acc) (local.get 0)))
                        (local.set $i (i32.add (local.get $i) (i32.const 1)))
                        (br $l)))
                    (local.get $acc)))
            """)
        )

        class HeavyStep:
            function = "heavy"
            input_columns = ["x"]
            output_column = "y"

        table = pa.table({"x": [1.0]})
        result = WasmExecutor().execute(table, HeavyStep(), heavy, fuel_budget=FUEL_PER_ROW)
        assert result.column("y").to_pylist() == [None]

    def test_infinite_loop_killed_without_hang(self, infinite_loop_wasm):
        table = pa.table({"x": [1.0] * 8})

        class MockStep:
            function = "infinite"
            input_columns = ["x"]
            output_column = "result"

        start = time.time()
        result = WasmExecutor().execute(table, MockStep(), infinite_loop_wasm)
        elapsed = time.time() - start
        assert elapsed < 5.0
        assert result.column("result").to_pylist() == [None] * 8

    def test_empty_table(self, simple_add_wasm):
        table = pa.table({"a": [], "b": []})

        class MockStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "sum"

        result = WasmExecutor().execute(table, MockStep(), simple_add_wasm)
        assert result.num_rows == 0
        assert result.column("sum").to_pylist() == []
