"""Tests for the healing sandbox."""

import inspect
from types import SimpleNamespace

import duckdb
import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.execution.sandbox import (
    SANDBOX_SAMPLE_ROWS,
    _require_input_table,
    _run_pipeline_in_sandbox,
    run_patch_in_sandbox,
)
from backend.pipeline.cache import get_parsed_pipeline


def test_sandbox_uses_expected_sample_size():
    assert SANDBOX_SAMPLE_ROWS == 100


def test_sandbox_source_uses_fresh_duckdb_connection_and_finally_close():
    source = inspect.getsource(run_patch_in_sandbox)
    assert 'duckdb.connect(database=":memory:")' in source
    assert "finally" in source
    assert "connection.close()" in source


def test_sandbox_does_not_use_worker_connection():
    source = inspect.getsource(run_patch_in_sandbox)
    assert "get_worker_duckdb" not in source


def test_run_pipeline_in_sandbox_executes_filter_and_save():
    yaml_text = """
pipeline:
  name: sandbox_test
  steps:
    - name: load_data
      type: load
      file_id: file-1
    - name: filter_high
      type: filter
      input: load_data
      column: amount
      operator: greater_than
      value: 100
    - name: save_output
      type: save
      input: filter_high
      filename: out.csv
""".strip()
    config = get_parsed_pipeline(yaml_text)
    sample_table = pa.Table.from_pandas(
        pd.DataFrame({"amount": [10, 150, 250], "region": ["N", "S", "E"]}),
        preserve_index=False,
    )

    connection = duckdb.connect(database=":memory:")
    try:
        executor = DuckDBExecutor(
            connection_getter=lambda: connection,
            local_fallback=False)
        result_table = _run_pipeline_in_sandbox(
            config=config,
            executor=executor,
            sampled_tables={"file-1": sample_table},
        )
    finally:
        connection.close()

    result_frame = result_table.to_pandas()
    assert result_frame["amount"].tolist() == [150, 250]


def test_run_pipeline_in_sandbox_validates_rename_columns():
    config = SimpleNamespace(
        steps=[
            SimpleNamespace(
                name="load_data",
                step_type="load",
                file_id="file-1"),
            SimpleNamespace(
                name="rename_data",
                step_type="rename",
                input="load_data",
                mapping={
                    "amount": "revenue"},
            ),
        ])
    sample_table = pa.Table.from_pandas(
        pd.DataFrame({"amount": [10, 20]}),
        preserve_index=False,
    )
    connection = duckdb.connect(database=":memory:")
    try:
        executor = DuckDBExecutor(
            connection_getter=lambda: connection,
            local_fallback=False)
        result_table = _run_pipeline_in_sandbox(
            config=config,
            executor=executor,
            sampled_tables={"file-1": sample_table},
        )
    finally:
        connection.close()

    assert result_table.column_names == ["revenue"]


def test_run_pipeline_in_sandbox_executes_aggregate():
    yaml_text = """
pipeline:
  name: sandbox_agg
  steps:
    - name: load_data
      type: load
      file_id: file-1
    - name: agg_step
      type: aggregate
      input: load_data
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
    - name: save_output
      type: save
      input: agg_step
      filename: out.csv
""".strip()
    config = get_parsed_pipeline(yaml_text)
    sample_table = pa.Table.from_pandas(
        pd.DataFrame({
            "amount": [100.0, 200.0, 50.0, 300.0, 150.0],
            "region": ["N", "S", "N", "E", "S"],
        }),
        preserve_index=False,
    )

    connection = duckdb.connect(database=":memory:")
    try:
        executor = DuckDBExecutor(
            connection_getter=lambda: connection,
            local_fallback=False)
        result_table = _run_pipeline_in_sandbox(
            config=config,
            executor=executor,
            sampled_tables={"file-1": sample_table},
        )
    finally:
        connection.close()

    assert result_table.num_rows == 3


def test_require_input_table_raises_for_missing_input():
    registry: dict[str, pa.Table] = {}
    with pytest.raises(ValueError, match="not available in the sandbox"):
        _require_input_table("step_x", "missing_input", registry)


def test_require_input_table_returns_table_when_present():
    table = pa.table({"col": [1, 2]})
    registry = {"my_input": table}
    result = _require_input_table("step_x", "my_input", registry)
    assert result is table


def test_sandbox_result_has_expected_fields():
    from backend.execution.sandbox import SandboxResult

    result = SandboxResult(success=True, output_rows=50, duration_ms=12.5)
    assert result.success is True
    assert result.output_rows == 50
    assert result.output_columns == []
    assert result.error is None
    assert result.duration_ms == 12.5


@pytest.mark.skipif(
    "True",
    reason="Requires module monkeypatching of imports — tested via integration",
)
def test_run_patch_in_sandbox_loads_sample_from_storage():
    pass
