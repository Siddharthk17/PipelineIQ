"""Regression tests for readv3-correctness N2.

N2 (Medium): ``stratify_by`` was silently ignored on the DuckDB path —
``build_sample_sql`` has no stratify branch, so the same YAML yielded
stratified samples below the ~50k-row routing threshold and uniform
reservoir samples above it. The chosen fix (already present via C2-009)
forces the pandas execution path whenever a sample step carries
``stratify_by``, so behavior is uniform everywhere; these tests pin it.

Fail-before evidence: executing the large-table scenario below through
``DuckDBExecutor`` directly (the pre-fix path for >= threshold inputs)
with seed=1 yields B=0 — the 0.1% minority group vanishes — while the
smart router yields B=1. Removing the router guard would flip
``engine`` to "duckdb" and drop group B here.
"""

import pandas as pd
import pyarrow as pa

from backend.execution.smart_executor import SmartExecutor
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import SampleStepConfig, StepType


def _sample_cfg(n, seed, stratify_by="g"):
    return SampleStepConfig(
        name="s",
        step_type=StepType.SAMPLE,
        input="t",
        n=n,
        random_state=seed,
        stratify_by=stratify_by,
    )


def _two_group_table(n_a, n_b):
    df = pd.DataFrame(
        {"g": ["A"] * n_a + ["B"] * n_b, "v": range(n_a + n_b)}
    )
    return pa.Table.from_pandas(df, preserve_index=False)


def test_n2_small_table_stratified_on_pandas_path():
    tbl = _two_group_table(80, 20)
    result = SmartExecutor().execute(
        _sample_cfg(n=20, seed=1), {"t": tbl}, LineageRecorder()
    )
    assert result.engine == "pandas"
    groups = result.output_table.column("g").to_pylist()
    assert len(groups) == 20
    # Proportional: 80/20 split -> both groups represented.
    assert groups.count("A") == 16
    assert groups.count("B") == 4


def test_n2_large_table_stratify_forces_pandas_path():
    # 60k rows >= DUCKDB_THRESHOLD would route to DuckDB without the
    # stratify guard; the guard must force pandas for uniform behavior.
    tbl = _two_group_table(59_940, 60)
    result = SmartExecutor().execute(
        _sample_cfg(n=100, seed=1), {"t": tbl}, LineageRecorder()
    )
    assert result.engine == "pandas"
    groups = result.output_table.column("g").to_pylist()
    assert len(groups) == 100
    # max(1, proportional) guarantee: the 0.1% minority survives.
    # (Direct DuckDB execution with this seed yields B=0.)
    assert groups.count("B") >= 1


def test_n2_single_group_stays_single_group():
    tbl = _two_group_table(100, 0)
    result = SmartExecutor().execute(
        _sample_cfg(n=10, seed=1), {"t": tbl}, LineageRecorder()
    )
    assert result.engine == "pandas"
    groups = result.output_table.column("g").to_pylist()
    assert len(groups) == 10
    assert set(groups) == {"A"}
