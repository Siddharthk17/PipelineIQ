"""Regression tests for M-032: user regex ReDoS in pandas filter paths.

``contains``/``not_contains`` previously passed the user value to
``Series.str.contains`` with ``regex=True`` (the pandas default), so a value
like ``(a+)+$`` caused catastrophic backtracking per row in workers. The
operators are now literal-substring matches: evil patterns complete fast and
match only literal text.
"""

import time

import pandas as pd
import pyarrow as pa

from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import FilterOperator, FilterStepConfig, StepType
from backend.pipeline.steps import StepExecutor


def _run_contains(value: str, cell: str) -> tuple[list, float]:
    df = pd.DataFrame({"s": [cell]})
    table = pa.Table.from_pandas(df, preserve_index=False)
    step = FilterStepConfig(
        name="f", step_type=StepType.FILTER, input="load",
        column="s", operator=FilterOperator.CONTAINS, value=value,
    )
    start = time.perf_counter()
    out = StepExecutor().execute_filter(
        {"load": table}, step, LineageRecorder()).output_df
    return out["s"].tolist(), time.perf_counter() - start


def test_m032_evil_pattern_completes_fast_and_matches_literally() -> None:
    """(a+)+$ against a near-miss string must not backtrack."""
    rows, elapsed = _run_contains("(a+)+$", "a" * 30 + "!")
    assert rows == []  # literal text "(a+)+$" is not present
    assert elapsed < 5.0


def test_m032_evil_pattern_matches_literal_text() -> None:
    """The same pattern matches when the literal text is present."""
    rows, _ = _run_contains("(a|a)*$", "xx (a|a)*$ yy")
    assert rows == ["xx (a|a)*$ yy"]


def test_m032_not_contains_evil_pattern_completes_fast() -> None:
    """not_contains shares the literal, ReDoS-free path."""
    df = pd.DataFrame({"s": ["a" * 30 + "!"]})
    table = pa.Table.from_pandas(df, preserve_index=False)
    step = FilterStepConfig(
        name="f", step_type=StepType.FILTER, input="load",
        column="s", operator=FilterOperator.NOT_CONTAINS, value="(a+)+$",
    )
    start = time.perf_counter()
    out = StepExecutor().execute_filter(
        {"load": table}, step, LineageRecorder()).output_df
    elapsed = time.perf_counter() - start
    assert out["s"].tolist() == ["a" * 30 + "!"]
    assert elapsed < 5.0
