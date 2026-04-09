"""Unit tests for DuckDB execution module."""

from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import (
    DuckDBExecutor,
    close_worker_duckdb,
    get_worker_duckdb,
    initialize_worker_duckdb,
)
from backend.pipeline.parser import FilterOperator, FilterStepConfig, JoinHow, JoinStepConfig


@pytest.fixture(autouse=True)
def _cleanup_worker_connection():
    close_worker_duckdb()
    yield
    close_worker_duckdb()


def test_get_worker_duckdb_raises_before_init() -> None:
    with pytest.raises(RuntimeError):
        get_worker_duckdb()


def test_initialize_worker_duckdb_returns_shared_connection() -> None:
    conn1 = initialize_worker_duckdb()
    conn2 = get_worker_duckdb()
    assert conn1 is conn2


def test_execute_filter_step_on_arrow_table() -> None:
    initialize_worker_duckdb()
    executor = DuckDBExecutor(local_fallback=False)
    step = FilterStepConfig(
        name="filter_high",
        step_type="filter",
        input="load",
        column="amount",
        operator=FilterOperator.GREATER_THAN,
        value=100,
    )
    df = pd.DataFrame({"amount": [10, 150, 250], "status": ["a", "b", "c"]})
    output = executor.execute_step(step, pa.Table.from_pandas(df, preserve_index=False))
    out_df = output.to_pandas()
    assert len(out_df) == 2
    assert out_df["amount"].min() > 100


def test_execute_join_step_uses_left_and_right_tables() -> None:
    initialize_worker_duckdb()
    executor = DuckDBExecutor(local_fallback=False)
    step = JoinStepConfig(
        name="join_data",
        step_type="join",
        left="left",
        right="right",
        on="id",
        how=JoinHow.INNER,
    )
    left = pa.Table.from_pandas(
        pd.DataFrame({"id": [1, 2], "amount": [100, 200]}),
        preserve_index=False,
    )
    right = pa.Table.from_pandas(
        pd.DataFrame({"id": [2, 3], "region": ["S", "N"]}),
        preserve_index=False,
    )
    output = executor.execute_step(
        step,
        left,
        extra_tables={"__left__": left, "__right__": right},
    )
    out_df = output.to_pandas()
    assert len(out_df) == 1
    assert out_df.iloc[0]["id"] == 2


def test_execute_sql_step_template_query() -> None:
    initialize_worker_duckdb()
    executor = DuckDBExecutor(local_fallback=False)
    step = SimpleNamespace(
        step_type="sql",
        query="SELECT customer_id, amount * 2 AS amount_x2 FROM {{input}}",
    )
    input_table = pa.Table.from_pandas(
        pd.DataFrame({"customer_id": [1, 2], "amount": [10, 20]}),
        preserve_index=False,
    )
    output = executor.execute_step(step, input_table)
    out_df = output.to_pandas()
    assert list(out_df.columns) == ["customer_id", "amount_x2"]
    assert out_df["amount_x2"].tolist() == [20, 40]

