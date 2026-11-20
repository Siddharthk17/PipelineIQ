"""Regression tests for M-130: per-row fuel cap nulls non-trivial UDFs.

Bug: the executor reset the store fuel to FUEL_PER_ROW (1000) before every
row, so any UDF needing more than ~1000 fuel units per call (e.g. a small
loop) trapped and produced None — while the module's configured
fuel_budget was ignored entirely. The fix distributes the step/module
fuel budget across rows so legitimate UDFs run and the budget is honored.
"""

import pyarrow as pa
from wasmtime import wat2wasm

from backend.execution.wasm_executor import FUEL_PER_ROW, FUEL_PER_STEP, WasmExecutor

# A bounded loop: 5000 iterations of f64 accumulation. Needs far more than
# FUEL_PER_ROW=1000 fuel units per call, but trivially fits in the step
# budget. Pre-fix every row comes back None.
HEAVY_WASM = bytes(
    wat2wasm(
        """
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
            (local.get $acc))
        )
        """
    )
)


class _HeavyStep:
    function = "heavy"
    input_columns = ["x"]
    output_column = "y"


class TestFuelBudgetHonored:
    def test_nontrivial_udf_produces_values(self):
        table = pa.table({"x": [1.0, 2.0]})
        result = WasmExecutor().execute(table, _HeavyStep(), bytes(HEAVY_WASM))
        assert result.column("y").to_pylist() == [5000.0, 10000.0]

    def test_explicit_small_budget_still_constrains(self):
        """A tiny explicit budget must still trap (budget is honored)."""
        table = pa.table({"x": [1.0]})
        result = WasmExecutor().execute(
            table, _HeavyStep(), bytes(HEAVY_WASM), fuel_budget=FUEL_PER_ROW
        )
        assert result.column("y").to_pylist() == [None]

    def test_explicit_step_budget_used(self):
        table = pa.table({"x": [1.0, 2.0, 3.0]})
        result = WasmExecutor().execute(
            table, _HeavyStep(), bytes(HEAVY_WASM), fuel_budget=FUEL_PER_STEP
        )
        assert result.column("y").to_pylist() == [5000.0, 10000.0, 15000.0]

    def test_step_object_fuel_budget_attribute_honored(self):
        """fuel_budget carried on the step object is picked up."""

        class StepWithBudget:
            function = "heavy"
            input_columns = ["x"]
            output_column = "y"
            fuel_budget = FUEL_PER_STEP

        table = pa.table({"x": [1.0]})
        result = WasmExecutor().execute(table, StepWithBudget(), bytes(HEAVY_WASM))
        assert result.column("y").to_pylist() == [5000.0]
