"""Regression tests for M-014/M-161: contains is literal, LIKE escaped.

pandas ``str.contains`` used regex semantics while DuckDB used unescaped
``LIKE`` (``%``/``_`` wildcards), so ``contains "50%"`` matched 5 rows below
the engine threshold and every row above it. Both engines now implement
literal-substring ``contains``.
"""

from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.execution.sql_builder import build_filter_sql
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    FilterOperator,
    FilterStepConfig,
    StepType,
)
from backend.pipeline.steps import StepExecutor


@pytest.mark.parametrize("value", ["50%", "a_b", "back\\slash", "50%%", "%_%"])
def test_m014_like_pattern_escapes_wildcards(value: str) -> None:
    """Generated LIKE escapes %, _ and \\ and declares an ESCAPE char."""
    sql = build_filter_sql(
        SimpleNamespace(column="note", operator="contains", value=value))
    assert "ESCAPE" in sql
    literal = sql.split("LIKE", 1)[1]
    assert value not in literal  # raw value must not appear unescaped


def test_m014_contains_percent_matches_literally_on_both_engines() -> None:
    """contains '50%' matches only literal rows in pandas AND DuckDB."""
    df = pd.DataFrame({"note": ["50% off", "500 off", "50X off", "half"]})
    table = pa.Table.from_pandas(df, preserve_index=False)
    step = FilterStepConfig(
        name="f", step_type=StepType.FILTER, input="load",
        column="note", operator=FilterOperator.CONTAINS, value="50%",
    )
    pandas_out = StepExecutor().execute_filter(
        {"load": table}, step, LineageRecorder()).output_df
    duck_exec = DuckDBExecutor()
    duck_out = duck_exec.execute_step(step, table).to_pandas()
    duck_exec.close()
    assert pandas_out["note"].tolist() == ["50% off"]
    assert duck_out["note"].tolist() == ["50% off"]


@pytest.mark.parametrize("op", ["contains", "not_contains"])
def test_m014_regex_metachars_are_literal(op: str) -> None:
    """Regex syntax in the value (a.c, (a+)+$) matches literally."""
    df = pd.DataFrame({"s": ["a.c", "abc", "axc", "(a+)+$"]})
    table = pa.Table.from_pandas(df, preserve_index=False)
    step = FilterStepConfig(
        name="f", step_type=StepType.FILTER, input="load",
        column="s", operator=FilterOperator(op), value="a.c",
    )
    pandas_out = StepExecutor().execute_filter(
        {"load": table}, step, LineageRecorder()).output_df
    duck_exec = DuckDBExecutor()
    duck_out = duck_exec.execute_step(step, table).to_pandas()
    duck_exec.close()
    if op == "contains":
        assert pandas_out["s"].tolist() == ["a.c"]
        assert duck_out["s"].tolist() == ["a.c"]
    else:
        assert sorted(pandas_out["s"].tolist()) == ["(a+)+$", "abc", "axc"]
        assert sorted(duck_out["s"].tolist()) == ["(a+)+$", "abc", "axc"]


def test_m014_underscore_does_not_act_as_wildcard() -> None:
    """contains 'a_b' matches literal 'a_b' only, on either engine."""
    df = pd.DataFrame({"s": ["a_b", "axb", "a__b"]})
    table = pa.Table.from_pandas(df, preserve_index=False)
    step = FilterStepConfig(
        name="f", step_type=StepType.FILTER, input="load",
        column="s", operator=FilterOperator.CONTAINS, value="a_b",
    )
    pandas_out = StepExecutor().execute_filter(
        {"load": table}, step, LineageRecorder()).output_df
    duck_exec = DuckDBExecutor()
    duck_out = duck_exec.execute_step(step, table).to_pandas()
    duck_exec.close()
    assert sorted(pandas_out["s"].tolist()) == ["a_b"]
    assert sorted(duck_out["s"].tolist()) == ["a_b"]
