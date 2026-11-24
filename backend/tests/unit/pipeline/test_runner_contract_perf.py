"""M-060: runner must not convert Arrow->pandas when a step has no contract.

validate_step_contract(None, df) returns [] without touching df, so the
unconditional output_table.to_pandas() in _execute_single_step is pure
waste (measured ~3ms/200k rows per step, scales with table size).
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pyarrow as pa

from backend.pipeline.runner import PipelineRunner
from backend.pipeline.steps import StepExecutionResult


class _FakeBus:
    def put(self, **kwargs):
        pass


class _SpyTable:
    """Wraps a pa.Table and counts to_pandas() calls (M-060 regression)."""

    def __init__(self, table: pa.Table):
        self._table = table
        self.calls = 0

    @property
    def num_rows(self) -> int:
        return self._table.num_rows

    @property
    def schema(self):
        return self._table.schema

    def to_pandas(self, *args, **kwargs):
        self.calls += 1
        return self._table.to_pandas(*args, **kwargs)


def _make_runner(monkeypatch, table, to_dict_spy=None):
    runner = PipelineRunner.__new__(PipelineRunner)
    from backend.pipeline.lineage import LineageRecorder

    recorder = LineageRecorder()
    result = StepExecutionResult(
        step_name="s",
        step_type="filter",
        output_table=table,
        rows_in=table.num_rows,
        rows_out=table.num_rows,
        columns_in=table.schema.names,
        columns_out=table.schema.names,
        duration_ms=1,
    )
    runner._executor = MagicMock()
    runner._executor.execute.return_value = result
    runner._executor._current_run_id = None
    return runner, recorder, result


def _run_step(monkeypatch, table, contract):
    spy = _SpyTable(table)
    runner, recorder, _ = _make_runner(monkeypatch, spy)
    step = SimpleNamespace(
        name="s", step_type="filter", input="load", contract=contract)
    out = runner._execute_single_step(
        step=step, index=0, total_steps=1, run_id="r",
        table_registry={}, recorder=recorder,
        file_paths={}, file_metadata={},
        callback=lambda e: None, lineage_callback=None, bus=_FakeBus(),
    )
    return out, spy


def test_no_pandas_conversion_without_contract(monkeypatch):
    table = pa.table({"a": [1, 2, 3]})
    out, spy = _run_step(monkeypatch, table, contract=None)
    assert out.contract_violations == []
    assert spy.calls == 0, f"to_pandas called {spy.calls}x with no contract"


def test_pandas_conversion_with_contract(monkeypatch):
    table = pa.table({"a": [1, 2, 3]})
    contract = {"columns": [{"name": "a", "required": True}]}
    out, spy = _run_step(monkeypatch, table, contract=contract)
    assert out.contract_violations == []
    assert spy.calls == 1
