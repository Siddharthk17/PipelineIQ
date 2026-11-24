"""Regression tests for M-013: DuckDB join must match pandas columns/values.

pandas join uses suffixes=("_left", "_right"); the legacy DuckDB
``SELECT * ... USING`` produced ``val``/``val_1`` (or duplicate ``val`` in
Arrow), silently changing results above the 50k-row DuckDB threshold.
"""

import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import JoinHow, JoinStepConfig, StepType
from backend.pipeline.steps import StepExecutor

HOWS = [JoinHow.INNER, JoinHow.LEFT, JoinHow.RIGHT, JoinHow.OUTER]


def _norm(df: pd.DataFrame) -> list[tuple]:
    """Order-insensitive row encoding (NULL-safe) for engine comparison."""
    filled = df.fillna("\x00NULL\x00").astype(str)
    return sorted(map(tuple, filled.itertuples(index=False, name=None)))


def _run_both(left: pd.DataFrame, right: pd.DataFrame, how: JoinHow):
    pandas_exec = StepExecutor()
    duck_exec = DuckDBExecutor()
    recorder = LineageRecorder()
    left_t = pa.Table.from_pandas(left, preserve_index=False)
    right_t = pa.Table.from_pandas(right, preserve_index=False)
    step = JoinStepConfig(
        name="j", step_type=StepType.JOIN,
        left="l", right="r", on="k", how=how,
    )
    pandas_out = pandas_exec.execute_join(
        {"l": left_t, "r": right_t}, step, recorder).output_df
    duck_out = duck_exec.execute_step(
        step, left_t,
        extra_tables={"__left__": left_t, "__right__": right_t},
    ).to_pandas()
    duck_exec.close()
    return pandas_out, duck_out


@pytest.mark.parametrize("how", HOWS)
def test_m013_join_columns_and_values_match_pandas(how: JoinHow) -> None:
    """Overlapping non-key columns keep pandas names/order/values."""
    left = pd.DataFrame({"k": [1, 2, 3], "v": ["lv1", "lv2", "lv3"]})
    right = pd.DataFrame({"k": [2, 3, 4], "v": ["rv2", "rv3", "rv4"]})
    pandas_out, duck_out = _run_both(left, right, how)
    assert list(duck_out.columns) == list(pandas_out.columns)
    assert list(duck_out.columns).count("v") == 0  # no ambiguous duplicates
    assert _norm(duck_out) == _norm(pandas_out)


@pytest.mark.parametrize("how", HOWS)
def test_m013_join_null_keys_match_pandas(how: JoinHow) -> None:
    """NULL keys match each other (pandas merge semantics), on either engine.

    Uses float NaN keys (the CSV-load case). DuckDB uses null-safe
    ``IS NOT DISTINCT FROM`` key matching so NaN/None keys pair exactly
    as pandas pairs them.
    """
    left = pd.DataFrame({"k": [float("nan"), 1.0], "v": ["lv0", "lv1"]})
    right = pd.DataFrame({"k": [float("nan"), 1.0], "v": ["rv0", "rv1"]})
    pandas_out, duck_out = _run_both(left, right, how)
    assert list(duck_out.columns) == list(pandas_out.columns)
    assert _norm(duck_out) == _norm(pandas_out)


def test_m013_join_no_overlap_unchanged() -> None:
    """Disjoint schemas keep working with identical output."""
    left = pd.DataFrame({"k": [1, 2], "a": ["x", "y"]})
    right = pd.DataFrame({"k": [2, 3], "b": ["p", "q"]})
    pandas_out, duck_out = _run_both(left, right, JoinHow.LEFT)
    assert list(duck_out.columns) == list(pandas_out.columns)
    assert _norm(duck_out) == _norm(pandas_out)
