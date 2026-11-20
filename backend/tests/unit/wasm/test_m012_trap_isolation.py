"""Regression tests for M-012: WASM traps must not NULL input columns.

Bug: when a Wasm UDF trapped on a row, the executor appended None for
*every* input column as well as the output column, silently destroying
input data. A trap must isolate to the output column only (input values
preserved). A step whose output_column collides with an input column must
be rejected loudly instead of producing duplicated/corrupt rows.
"""

import pyarrow as pa
import pytest
from wasmtime import wat2wasm

from backend.execution.wasm_executor import WasmExecutor

TRAP_WASM = bytes(wat2wasm('(module (func (export "boom") (param f64) (result f64) unreachable))'))


def _trap_bytes() -> bytes:
    return bytes(TRAP_WASM)


class _BoomStep:
    function = "boom"
    input_columns = ["x"]
    output_column = "y"


class TestTrapIsolation:
    def test_trap_nulls_only_output_column(self):
        table = pa.table({"x": [1.0, 2.0, 3.0]})
        result = WasmExecutor().execute(table, _BoomStep(), _trap_bytes())
        assert result.num_rows == 3
        # Input column must be preserved verbatim — never nulled by a trap.
        assert result.column("x").to_pylist() == [1.0, 2.0, 3.0]
        assert result.column("y").to_pylist() == [None, None, None]

    def test_trap_preserves_all_input_columns(self):
        table = pa.table({"a": [1.0, 2.0], "b": [10.0, 20.0]})

        class Step:
            function = "boom"
            input_columns = ["a", "b"]
            output_column = "out"

        boom2 = bytes(
            wat2wasm('(module (func (export "boom") (param f64 f64) (result f64) unreachable))')
        )
        result = WasmExecutor().execute(table, Step(), boom2)
        assert result.num_rows == 2
        assert result.column("a").to_pylist() == [1.0, 2.0]
        assert result.column("b").to_pylist() == [10.0, 20.0]
        assert result.column("out").to_pylist() == [None, None]

    def test_output_column_equal_to_input_is_rejected(self, simple_add_wasm):
        table = pa.table({"a": [1.0, 2.0], "b": [10.0, 20.0]})

        class BadStep:
            function = "add"
            input_columns = ["a", "b"]
            output_column = "a"

        with pytest.raises(ValueError, match="output_column"):
            WasmExecutor().execute(table, BadStep(), simple_add_wasm)
