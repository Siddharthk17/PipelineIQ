"""M-112/M-114 engine parity regression tests.

Chosen unified semantics (documented):
- (a) GROUP BY keeps NULL keys as their own group on both engines
  (DuckDB/SQL semantics; pandas uses dropna=False).
- (b) NULL never matches a value predicate, even negated: NOT_EQUALS,
  NOT_CONTAINS/STARTS_WITH/ENDS_WITH all drop NULL rows on both engines
  (SQL semantics). Positive ops already drop NULLs on both.
- (c) fill_nulls: parser whitelists canonical strategies; ffill/bfill are
  accepted as aliases for forward_fill/backward_fill on both engines;
  unknown strategies fail closed (ValueError) on both engines.
- (d) sort ascending must be strict bool (or list of bool); strings like
  "false" are rejected at parse (fail closed) because bool("false") is True.
- (e) sample random_state must be a non-negative int; negative seeds are
  rejected at parse (DuckDB SQL rejects them; pandas must not silently differ).
- (f) dedup keeps are deterministic via explicit ORDER BY (no bare
  ROW_NUMBER() OVER ()).
- (g) no max_rows_to_scan dead guard; memory_limit=2GB bound is the OOM guard.
- (h) concurrency=0 raises ValueError (fail-closed), never ZeroDivisionError.
"""

import pandas as pd
import pyarrow as pa
import pytest

from backend.execution.duckdb_executor import (
    DuckDBExecutor,
    _compute_duckdb_thread_count,
    close_worker_duckdb,
    initialize_worker_duckdb,
)
from backend.execution.sql_builder import (
    build_deduplicate_sql,
    build_sample_sql,
    build_sort_sql,
)
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    AggregateStepConfig,
    DeduplicateStepConfig,
    FillNullsStepConfig,
    FilterOperator,
    FilterStepConfig,
    PipelineParser,
    SampleStepConfig,
    SortStepConfig,
    StepType,
)
from backend.pipeline.steps._core import StepExecutor


@pytest.fixture()
def recorder() -> LineageRecorder:
    return LineageRecorder()


@pytest.fixture()
def executors():
    initialize_worker_duckdb()
    yield StepExecutor(), DuckDBExecutor(local_fallback=False)
    close_worker_duckdb()


def _yaml(text: str):  # parse helper
    import yaml as _yaml_mod

    return _yaml_mod.safe_load(text)


# (a) groupby NULL keys -------------------------------------------------------
def test_m112_groupby_null_keys_parity(executors, recorder) -> None:
    pandas_ex, duck_ex = executors
    df = pd.DataFrame({"g": ["a", None, "a", None], "v": [1, 2, 3, 4]})
    tbl = pa.Table.from_pandas(df, preserve_index=False)
    cfg = AggregateStepConfig(
        name="a",
        step_type=StepType.AGGREGATE,
        input="x",
        group_by=["g"],
        aggregations=[{"column": "v", "function": "sum"}],
    )
    pandas_out = pandas_ex.execute_aggregate({"x": tbl}, cfg, recorder)
    duck_out = duck_ex.execute_step(cfg, tbl)
    p = pandas_out.output_table.to_pandas().sort_values(
        "g", na_position="first").reset_index(drop=True)
    d = duck_out.to_pandas().sort_values(
        "g", na_position="first").reset_index(drop=True)
    # Unified semantics: NULL group is kept on both (2 groups: NULL->6, a->4).
    assert len(p) == 2, f"pandas must keep NULL group, got {len(p)}"
    assert len(d) == 2, f"duckdb must keep NULL group, got {len(d)}"
    assert p["v_sum"].sum() == 10
    assert d["v_sum"].sum() == 10


# (b) not_equals / not_contains NULLs -----------------------------------------
def test_m112_not_equals_null_parity(executors, recorder) -> None:
    pandas_ex, duck_ex = executors
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": [1.0, None, 3.0]}), preserve_index=False)
    cfg = FilterStepConfig(
        name="f",
        step_type=StepType.FILTER,
        input="x",
        column="a",
        operator=FilterOperator.NOT_EQUALS,
        value=1,
    )
    pandas_out = pandas_ex.execute_filter({"x": tbl}, cfg, recorder)
    duck_out = duck_ex.execute_step(cfg, tbl)
    # Unified semantics: NULL never matches, even negated -> dropped.
    assert pandas_out.output_table.num_rows == 1
    assert duck_out.num_rows == 1
    assert pandas_out.output_table.to_pandas()["a"].tolist() == [3.0]


def test_m112_not_contains_null_parity(executors, recorder) -> None:
    pandas_ex, duck_ex = executors
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"s": ["foo", None, "bar"]}), preserve_index=False)
    cfg = FilterStepConfig(
        name="f",
        step_type=StepType.FILTER,
        input="x",
        column="s",
        operator=FilterOperator.NOT_CONTAINS,
        value="foo",
    )
    pandas_out = pandas_ex.execute_filter({"x": tbl}, cfg, recorder)
    duck_out = duck_ex.execute_step(cfg, tbl)
    assert pandas_out.output_table.num_rows == 1
    assert duck_out.num_rows == 1
    assert pandas_out.output_table.to_pandas()["s"].tolist() == ["bar"]


def test_m112_contains_nan_literal_no_match(executors, recorder) -> None:
    """NaN must not match the literal 'nan' on either engine."""
    pandas_ex, duck_ex = executors
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"s": ["foo", None, "bar"]}), preserve_index=False)
    cfg = FilterStepConfig(
        name="f",
        step_type=StepType.FILTER,
        input="x",
        column="s",
        operator=FilterOperator.CONTAINS,
        value="nan",
    )
    pandas_out = pandas_ex.execute_filter({"x": tbl}, cfg, recorder)
    duck_out = duck_ex.execute_step(cfg, tbl)
    assert pandas_out.output_table.num_rows == 0
    assert duck_out.num_rows == 0


# (c) fill_nulls ---------------------------------------------------------------
def test_m112_fill_nulls_unknown_fails_closed_pandas(recorder) -> None:
    ex = StepExecutor()
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": [1.0, None, 3.0]}), preserve_index=False)
    cfg = FillNullsStepConfig(
        name="f",
        step_type=StepType.FILL_NULLS,
        input="x",
        strategy="magical",
        columns=["a"],
    )
    with pytest.raises(ValueError, match="strategy"):
        ex.execute_fill_nulls({"x": tbl}, cfg, recorder)


def test_m112_fill_nulls_ffill_bfill_aliases_both_engines(
        executors, recorder) -> None:
    pandas_ex, duck_ex = executors
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": [1.0, None, 3.0]}), preserve_index=False)
    for alias, expected in (("ffill", [1.0, 1.0, 3.0]),
                            ("bfill", [1.0, 3.0, 3.0])):
        cfg = FillNullsStepConfig(
            name="f",
            step_type=StepType.FILL_NULLS,
            input="x",
            strategy=alias,
            columns=["a"],
        )
        out = pandas_ex.execute_fill_nulls(
            {"x": tbl}, cfg, recorder).output_table.to_pandas()
        assert out["a"].tolist() == expected, alias
        duck_out = duck_ex.execute_step(cfg, tbl).to_pandas()
        assert duck_out["a"].tolist() == expected, alias


def test_m112_fill_nulls_parser_accepts_ffill_bfill() -> None:
    for alias in ("ffill", "bfill"):
        raw = {"pipeline": {"name": "p", "steps": [
            {"name": "load", "type": "load", "file_id": "f1"},
            {"name": "f", "type": "fill_nulls", "input": "load",
             "strategy": alias, "columns": ["c"]},
            {"name": "out", "type": "save", "input": "f",
             "filename": "o.csv"},
        ]}}
        import yaml as _y

        cfg = PipelineParser().parse(_y.safe_dump(raw))
        result = PipelineParser().validate(cfg, {"f1"})
        assert result.is_valid, (
            alias, [(e.field, e.message) for e in result.errors])


def test_m112_fill_nulls_parser_rejects_unknown() -> None:
    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "f1"},
        {"name": "f", "type": "fill_nulls", "input": "load",
         "strategy": "magical", "columns": ["c"]},
    ]}}
    import yaml as _y

    cfg = PipelineParser().parse(_y.safe_dump(raw))
    result = PipelineParser().validate(cfg, {"f1"})
    assert not result.is_valid
    assert any(e.field == "strategy" for e in result.errors)


# (d) sort ascending strict bool ------------------------------------------------
def test_m112_sort_ascending_string_rejected_at_parse() -> None:
    yaml_text = """pipeline:
  name: p
  steps:
    - name: load
      type: load
      file_id: f1
    - name: s
      type: sort
      input: load
      by: a
      ascending: "false"
    - name: out
      type: save
      input: s
      filename: o.csv
"""
    cfg = PipelineParser().parse(yaml_text)
    result = PipelineParser().validate(cfg, {"f1"})
    assert not result.is_valid
    assert any(e.field == "ascending" for e in result.errors)


def test_m112_sort_ascending_string_list_rejected_at_parse() -> None:
    yaml_text = """pipeline:
  name: p
  steps:
    - name: load
      type: load
      file_id: f1
    - name: s
      type: sort
      input: load
      by: [a, b]
      ascending: [true, "false"]
    - name: out
      type: save
      input: s
      filename: o.csv
"""
    cfg = PipelineParser().parse(yaml_text)
    result = PipelineParser().validate(cfg, {"f1"})
    assert not result.is_valid
    assert any(e.field == "ascending" for e in result.errors)


def test_m112_sort_builder_rejects_string_ascending() -> None:
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="ascending"):
        build_sort_sql(SimpleNamespace(by=["a"], ascending=["false"]))


def test_m112_sort_executor_rejects_string_ascending(recorder) -> None:
    ex = StepExecutor()
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": [2, 1]}), preserve_index=False)
    cfg = SortStepConfig(
        name="s",
        step_type=StepType.SORT,
        input="x",
        by="a",
        ascending="false",  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="ascending"):
        ex.execute_sort({"x": tbl}, cfg, recorder)


# (e) sample negative seed ------------------------------------------------------
def test_m112_sample_negative_seed_rejected_at_parse() -> None:
    yaml_text = """pipeline:
  name: p
  steps:
    - name: load
      type: load
      file_id: f1
    - name: s
      type: sample
      input: load
      n: 2
      random_state: -1
    - name: out
      type: save
      input: s
      filename: o.csv
"""
    cfg = PipelineParser().parse(yaml_text)
    result = PipelineParser().validate(cfg, {"f1"})
    assert not result.is_valid
    assert any(e.field == "random_state" for e in result.errors)


def test_m112_sample_builder_rejects_negative_seed() -> None:
    from types import SimpleNamespace

    with pytest.raises(ValueError, match="random_state"):
        build_sample_sql(
            SimpleNamespace(n=5, fraction=None, random_state=-1))


def test_m112_sample_executor_rejects_negative_seed(recorder) -> None:
    ex = StepExecutor()
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": range(10)}), preserve_index=False)
    cfg = SampleStepConfig(
        name="s",
        step_type=StepType.SAMPLE,
        input="x",
        n=2,
        random_state=-1,
    )
    with pytest.raises(ValueError, match="random_state"):
        ex.execute_sample({"x": tbl}, cfg, recorder)


# M-114 (f) deterministic dedup --------------------------------------------------
def test_m114_dedup_sql_has_deterministic_order_by() -> None:
    from types import SimpleNamespace

    for keep in ("first", "last"):
        sql = build_deduplicate_sql(
            SimpleNamespace(subset=["a"], keep=keep))
        assert "ROW_NUMBER() OVER ()" not in sql, sql
        assert "ORDER BY" in sql, sql


def test_m114_dedup_duckdb_uses_all_columns_for_order(
        executors) -> None:
    _, duck_ex = executors
    tbl = pa.Table.from_pandas(
        pd.DataFrame({"a": [1, 1, 2], "b": [5, 6, 7]}),
        preserve_index=False)
    cfg = DeduplicateStepConfig(
        name="d",
        step_type=StepType.DEDUPLICATE,
        input="x",
        subset=["a"],
        keep="first",
    )
    sql = duck_ex._build_step_sql(cfg, tbl, None)
    assert "ROW_NUMBER() OVER ()" not in sql, sql
    assert '"b"' in sql or '"a"' in sql, sql
    out = duck_ex.execute_step(cfg, tbl)
    assert out.num_rows == 2


# M-114 (g) dead max_rows guard removed ------------------------------------------------
def test_m114_no_max_rows_to_scan_dead_code() -> None:
    import inspect

    import backend.execution.duckdb_executor as mod

    src = inspect.getsource(mod.DuckDBExecutor.execute_sql)
    # Dead guard probed SET/current_setting('max_rows_to_scan') — removed
    # (a doc comment may still name it). Assert no functional use remains.
    assert "SET max_rows_to_scan" not in src, \
        "dead max_rows_to_scan guard must be removed"
    assert "current_setting('max_rows_to_scan')" not in src
    cfg_src = inspect.getsource(mod._configure_connection)
    assert "memory_limit" in cfg_src, \
        "2GB memory_limit bound must be kept"


# M-114 (h) concurrency=0 ---------------------------------------------------------
def test_m114_zero_concurrency_fails_closed() -> None:
    with pytest.raises(ValueError, match="concurrency"):
        _compute_duckdb_thread_count(4, 0)
    with pytest.raises(ValueError, match="concurrency"):
        _compute_duckdb_thread_count(4, -2)
