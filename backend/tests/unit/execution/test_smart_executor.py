"""Unit tests for SmartExecutor routing decisions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pyarrow as pa

from backend.execution.smart_executor import SmartExecutor
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    FilterStepConfig,
    JoinHow,
    JoinStepConfig,
    LoadStepConfig,
    SaveStepConfig,
    SqlStepConfig,
    StepType,
)
from backend.pipeline.steps import StepExecutionResult


def _table(rows: int) -> pa.Table:
    return pa.table({"id": list(range(rows)), "value": list(range(rows))})


def _result(
        step_name: str,
        step_type: str,
        table: pa.Table) -> StepExecutionResult:
    return StepExecutionResult(
        step_name=step_name,
        step_type=step_type,
        output_table=table,
        rows_in=table.num_rows,
        rows_out=table.num_rows,
        columns_in=table.column_names,
        columns_out=table.column_names,
        duration_ms=1,
    )


def test_load_step_always_routes_to_pandas_load() -> None:
    pandas_exec = MagicMock()
    duckdb_exec = MagicMock()
    router = SmartExecutor(pandas_exec, duckdb_exec)
    output = _table(5)

    pandas_exec.execute_load.return_value = _result(
        "load_data", "load", output)
    step = LoadStepConfig(
        name="load_data",
        step_type=StepType.LOAD,
        file_id="file-1")

    router.execute(
        step=step,
        table_registry={},
        recorder=LineageRecorder(),
        file_paths={"file-1": "/tmp/file.csv"},
        file_metadata={"file-1": {"original_filename": "file.csv"}},
    )

    pandas_exec.execute_load.assert_called_once()
    duckdb_exec.execute_step.assert_not_called()


def test_save_step_stays_on_pandas_even_for_large_input() -> None:
    pandas_exec = MagicMock()
    duckdb_exec = MagicMock()
    router = SmartExecutor(pandas_exec, duckdb_exec)
    large_table = _table(SmartExecutor.DUCKDB_THRESHOLD + 1)

    pandas_exec.execute.return_value = _result(
        "save_data", "save", large_table)
    step = SaveStepConfig(
        name="save_data",
        step_type=StepType.SAVE,
        input="filtered",
        filename="out.csv",
    )

    router.execute(step, {"filtered": large_table}, LineageRecorder())

    pandas_exec.execute.assert_called_once()
    duckdb_exec.execute_step.assert_not_called()


def test_sql_step_always_routes_to_duckdb_even_for_small_input() -> None:
    pandas_exec = MagicMock()
    duckdb_exec = MagicMock()
    router = SmartExecutor(pandas_exec, duckdb_exec)
    small_table = _table(10)
    duckdb_exec.execute_step.return_value = small_table

    step = SqlStepConfig(
        name="sql_step",
        step_type=StepType.SQL,
        input="load",
        query="SELECT * FROM {{input}}",
    )
    router.execute(step, {"load": small_table}, LineageRecorder())

    duckdb_exec.execute_step.assert_called_once()
    pandas_exec.execute.assert_not_called()


def test_large_filter_routes_to_duckdb() -> None:
    pandas_exec = MagicMock()
    duckdb_exec = MagicMock()
    router = SmartExecutor(pandas_exec, duckdb_exec)
    large_table = _table(SmartExecutor.DUCKDB_THRESHOLD + 1)
    duckdb_exec.execute_step.return_value = large_table

    step = FilterStepConfig(
        name="filter_step",
        step_type=StepType.FILTER,
        input="load")
    router.execute(step, {"load": large_table}, LineageRecorder())

    duckdb_exec.execute_step.assert_called_once_with(
        step, large_table, extra_tables=None)
    pandas_exec.execute.assert_not_called()


def test_large_join_routes_to_duckdb_with_join_aliases() -> None:
    pandas_exec = MagicMock()
    duckdb_exec = MagicMock()
    router = SmartExecutor(pandas_exec, duckdb_exec)
    left = _table(SmartExecutor.DUCKDB_THRESHOLD + 10)
    right = _table(SmartExecutor.DUCKDB_THRESHOLD + 20)
    duckdb_exec.execute_step.return_value = left

    step = JoinStepConfig(
        name="join_step",
        step_type=StepType.JOIN,
        left="left_input",
        right="right_input",
        on="id",
        how=JoinHow.INNER,
    )
    router.execute(
        step, {
            "left_input": left, "right_input": right}, LineageRecorder())

    duckdb_exec.execute_step.assert_called_once()
    call = duckdb_exec.execute_step.call_args
    assert call.args[0] is step
    assert call.args[1] is left
    assert call.kwargs["extra_tables"]["__left__"] is left
    assert call.kwargs["extra_tables"]["__right__"] is right
    pandas_exec.execute.assert_not_called()


class TestC2006AggregateAliasParity:
    """C2-006: pandas and DuckDB must agree on output column names."""

    def test_func_named_like_column_suffixes(self):
        import pandas as pd

        from backend.pipeline.parser import AggregateStepConfig, StepType
        from backend.pipeline.steps._core import StepExecutor

        df = pd.DataFrame({"g": ["a", "a", "b"], "sum": [1, 2, 3]})
        cfg = AggregateStepConfig(
            name="agg", step_type=StepType.AGGREGATE, input="in",
            group_by=["g"], aggregations=[{"column": "sum", "function": "sum"}])
        import pyarrow as pa

        from backend.pipeline.lineage import LineageRecorder
        reg = {"in": pa.Table.from_pandas(df, preserve_index=False)}
        out = StepExecutor().execute_aggregate(reg, cfg, LineageRecorder())
        assert "sum_sum" in out.output_table.column_names
        assert "sum" not in [
            c for c in out.output_table.column_names if c != "g"]

    def test_explicit_alias_honored(self):
        import pandas as pd

        from backend.pipeline.parser import AggregateStepConfig, StepType
        from backend.pipeline.steps._core import StepExecutor

        df = pd.DataFrame({"g": ["a", "b"], "amount": [1, 2]})
        cfg = AggregateStepConfig(
            name="agg", step_type=StepType.AGGREGATE, input="in",
            group_by=["g"],
            aggregations=[{"column": "amount", "function": "sum",
                           "alias": "total"}])
        import pyarrow as pa

        from backend.pipeline.lineage import LineageRecorder
        reg = {"in": pa.Table.from_pandas(df, preserve_index=False)}
        out = StepExecutor().execute_aggregate(reg, cfg, LineageRecorder())
        assert "total" in out.output_table.column_names


class TestC2008PivotFillValue:
    """C2-008: DuckDB pivot must honor fill_value like pandas."""

    def _pivot_step(self):
        from backend.pipeline.parser import PivotStepConfig, StepType
        return PivotStepConfig(
            name="pv", step_type=StepType.PIVOT, input="in",
            index=["g"], columns="c", values="v", aggfunc="sum",
            fill_value=0)

    def test_duckdb_pivot_fills_missing_with_zero(self):
        import pandas as pd
        import pyarrow as pa

        from backend.execution.smart_executor import SmartExecutor
        from backend.pipeline.lineage import LineageRecorder

        df = pd.DataFrame({
            "g": ["a", "a", "b"], "c": ["x", "y", "x"], "v": [1, 2, 3]})
        reg = {"in": pa.Table.from_pandas(df, preserve_index=False)}
        ex = SmartExecutor()
        ex.DUCKDB_THRESHOLD = 0  # force DuckDB path
        out = ex.execute(
            self._pivot_step(), reg, LineageRecorder()).output_table
        assert out.to_pandas().isnull().sum().sum() == 0


class TestC2009StratifiedSampleStaysPandas:
    """C2-009: stratify_by must never route to DuckDB's uniform sampler."""

    def test_stratified_sample_uses_pandas_engine(self):
        import pandas as pd
        import pyarrow as pa

        from backend.execution.smart_executor import SmartExecutor
        from backend.pipeline.lineage import LineageRecorder
        from backend.pipeline.parser import SampleStepConfig, StepType

        df = pd.DataFrame({
            "g": ["a"] * 20 + ["b"] * 20,
            "v": list(range(40))})
        reg = {"in": pa.Table.from_pandas(df, preserve_index=False)}
        step = SampleStepConfig(
            name="s", step_type=StepType.SAMPLE, input="in",
            n=10, random_state=1, stratify_by="g")
        ex = SmartExecutor()
        ex.DUCKDB_THRESHOLD = 0  # would route to DuckDB without the guard
        result = ex.execute(step, reg, LineageRecorder())
        assert result.engine == "pandas"
        counts = result.output_table.to_pandas()["g"].value_counts()
        assert counts["a"] == 5 and counts["b"] == 5
