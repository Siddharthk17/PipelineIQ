"""Regression tests for W3 engine remediation findings.

Each test pins the fixed behavior for one finding. They were written
before the fixes (all fail on the pre-fix tree) per the mandated
regression-test-first procedure.
"""

import pytest
import yaml

from backend.pipeline.diff_utils import strip_diff_markers
from backend.pipeline.exceptions import (
    InvalidYAMLError,
    PipelineIQError,
)
from backend.pipeline.parser import PipelineParser
from backend.pipeline.validators import execute_validate


def _yaml(obj) -> str:
    return yaml.safe_dump(obj)


def _chained(n, **kw):
    steps = [{"name": "load", "type": "load", "file_id": "f1"}]
    for i in range(1, n):
        steps.append({
            "name": f"s{i}",
            "type": "filter",
            "input": "load" if i == 1 else f"s{i - 1}",
            "column": "c",
            "operator": "equals",
            "value": 1,
        })
    return {"pipeline": {"name": "p", "steps": steps}}


# --- M-015: early step-count cap -------------------------------------------
def test_m015_over_limit_returns_early_without_reference_errors():
    # 60 chained steps + one dangling reference at the end. The step-count
    # cap must fire first: no O(n^2) reference walk, no dangling-ref error.
    raw = _chained(60)
    raw["pipeline"]["steps"].append({
        "name": "dangling", "type": "filter", "input": "nope",
        "column": "c", "operator": "equals", "value": 1,
    })
    config = PipelineParser().parse(_yaml(raw))
    result = PipelineParser().validate(config, {"f1"})
    assert not result.is_valid
    assert any("exceeding the limit" in e.message for e in result.errors)
    assert not any("nope" in e.message for e in result.errors)


def test_m015_step_count_still_reported_as_validation_error():
    config = PipelineParser().parse(_yaml(_chained(51)))
    result = PipelineParser().validate(config, {"f1"})
    assert result.is_valid is False
    assert any("exceeding the limit" in e.message for e in result.errors)


# --- M-031: alias-chain error blowup ---------------------------------------
def test_m031_yaml_error_message_is_capped():
    huge = "x" * 100_000
    err = InvalidYAMLError(huge)
    assert len(err.message) < 5_000
    assert len(err.to_dict()["yaml_error"]) < 5_000


# --- M-041: diff stripper corrupts valid YAML -------------------------------
def test_m041_valid_yaml_with_doc_marker_and_lists_is_untouched():
    text = "---\n- item1\n- item2\nkey: value\n"
    assert strip_diff_markers(text) == text


def test_m041_col0_content_lines_preserved_without_hunks():
    text = "pipeline:\n  name: p\nsteps:\n- name: a\n  type: load\n"
    assert strip_diff_markers(text) == text


def test_m041_real_diff_still_stripped():
    diff = (
        "--- a/pipe.yaml\n+++ b/pipe.yaml\n@@ -1,3 +1,3 @@\n"
        " pipeline:\n"
        "-  name: old\n"
        "+  name: new\n"
        "   steps: []\n"
    )
    out = strip_diff_markers(diff)
    assert "name: new" in out
    assert "name: old" not in out
    assert "---" not in out and "+++" not in out and "@@" not in out


# --- M-100: nested YAML RecursionError --------------------------------------
def test_m100_deep_nesting_returns_400_not_recursion_error():
    depth = 500
    text = "pipeline:\n  name: p\n  steps: " + "[" * depth + "]" * depth
    with pytest.raises(PipelineIQError):
        PipelineParser().parse(text)


# --- M-110: single parse path for plan --------------------------------------
def test_m110_planner_strips_diff_markers_like_validate():
    from backend.pipeline.planner import generate_execution_plan

    class _FakeFile:
        id = "f1"
        row_count = 10
        columns = ["c"]

    class _FakeQuery:
        def filter(self, *a, **k):
            return self

        def all(self):
            return [_FakeFile()]

    class _FakeDB:
        def query(self, *a, **k):
            return _FakeQuery()

    clean = _yaml({
        "pipeline": {"name": "p", "steps": [
            {"name": "load", "type": "load", "file_id": "f1"},
        ]},
    })
    n_lines = len(clean.splitlines())
    diffed = (
        "--- a/p.yaml\n+++ b/p.yaml\n"
        f"@@ -1,{n_lines} +1,{n_lines} @@\n"
        + "".join(f"+{line}\n" for line in clean.splitlines())
    )
    plan_clean = generate_execution_plan(clean, _FakeDB())
    plan_diff = generate_execution_plan(diffed, _FakeDB())
    assert plan_diff.total_steps == plan_clean.total_steps == 1


def test_m110_planner_survives_malformed_steps_without_500():
    from backend.pipeline.planner import generate_execution_plan

    class _FakeDB:
        def query(self, *a, **k):
            class _Q:
                def filter(self, *a, **k):
                    return self

                def all(self):
                    return []
            return _Q()

    # steps as mapping, plus a non-dict step entry
    weird = _yaml({"pipeline": {"name": "p",
                               "steps": {"a": 1, "b": 2}}})
    plan = generate_execution_plan(weird, _FakeDB())
    assert plan.total_steps == 0

    listed = _yaml({"pipeline": {"name": "p",
                                "steps": ["oops",
                                          {"name": "x"}]}})
    plan2 = generate_execution_plan(listed, _FakeDB())
    assert plan2.total_steps == 2


# --- M-113: parser/validator unhandled exceptions ----------------------------
@pytest.mark.parametrize("step,field", [
    ({"name": "s", "type": "stream_consume", "topic": "t",
      "consumer_group": "g", "batch_size": "abc"}, "batch_size"),
    ({"name": "s", "type": "sql", "input": "load",
      "query": 123}, "query"),
    ({"name": "s", "type": "transform", "input": "load",
      "expression": ["x"]}, "expression"),
    ({"name": "s", "type": "load", "file_id": 123}, "file_id"),
    ({"name": "s", "type": "select", "input": "load",
      "columns": "col"}, "columns"),
    ({"name": "s", "type": "rename", "input": "load",
      "mapping": "a=b"}, "mapping"),
    ({"name": "s", "type": "pivot", "input": "load",
      "index": {"a": 1}, "columns": "c", "values": "v"}, "index"),
    ({"name": "s", "type": "sample", "input": "load",
      "n": "many"}, "sample"),
    ({"name": "s", "type": "sample", "input": "load",
      "fraction": 5}, "sample"),
    ({"name": "s", "type": "fill_nulls", "input": "load",
      "strategy": "magical", "columns": ["c"]}, "strategy"),
    ({"name": "s", "type": "validate", "input": "load",
      "rules": ["not-a-dict"]}, "rules"),
    ({"name": "s", "type": "validate", "input": "load",
      "rules": [{"column": "c"}]}, "rules"),
    ({"name": "s", "type": "aggregate", "input": "load",
      "group_by": ["x"], "aggregations": "sum"}, "aggregations"),
    ({"name": "s", "type": "join", "left": 1, "right": 2,
      "on": "k"}, "left"),
    ({"name": "s", "type": "filter", "input": 5, "column": "c",
      "operator": "equals", "value": 1}, "input"),
])
def test_m113_malformed_params_are_validation_errors(step, field):
    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "f1"}, step]}}
    config = PipelineParser().parse(_yaml(raw))
    result = PipelineParser().validate(config, {"f1"})
    assert not result.is_valid
    assert any(e.step_name == "s" and e.field.startswith(field)
               for e in result.errors), \
        [f"{e.step_name}:{e.field}:{e.message}" for e in result.errors]


def test_m113_group_by_yaml_true_key_rejected():    # YAML 1.1 `on:` parses as boolean True; must be a 400, not a crash.
    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "f1"},
        {"name": "a", "type": "aggregate", "input": "load",
         "group_by": [True], "aggregations": [{"column": "c",
                                              "function": "sum"}]},
    ]}}
    config = PipelineParser().parse(_yaml(raw))
    result = PipelineParser().validate(config, {"f1"})
    assert not result.is_valid
    assert any(e.step_name == "a" for e in result.errors)


def test_m113_execute_validate_never_raises():
    import pandas as pd
    df = pd.DataFrame({"c": [1, 2, 3]})
    bad_rules = [
        "not-a-dict",
        {"column": "c"},                      # missing check
        {"check": "greater_than", "column": "c"},  # missing value
        {"check": "no_such_check", "column": "c"},
        {"check": "greater_than", "column": "c", "value": object()},
        None,
    ]
    step_result = execute_validate(df, bad_rules, "s")
    assert step_result.passed is False
    assert len(step_result.rule_results) == len(bad_rules)


# --- M-143: same-filename save overwrite -------------------------------------
def test_m143_duplicate_save_filenames_rejected():
    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "f1"},
        {"name": "s1", "type": "save", "input": "load",
         "filename": "out.csv"},
        {"name": "s2", "type": "save", "input": "load",
         "filename": "out.csv"},
    ]}}
    config = PipelineParser().parse(_yaml(raw))
    result = PipelineParser().validate(config, {"f1"})
    assert not result.is_valid
    assert any(e.field == "filename" for e in result.errors)


# --- M-159: runner re-validates ----------------------------------------------
def test_m159_runner_rejects_invalid_config_before_executing():
    from backend.pipeline.exceptions import StepExecutionError
    from backend.pipeline.runner import PipelineRunner

    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "missing"},
    ]}}
    config = PipelineParser().parse(_yaml(raw))
    with pytest.raises(StepExecutionError):
        PipelineRunner().execute(config, file_paths={}, file_metadata={})


# --- M-164: YAML size cap ------------------------------------------------------
def test_m164_oversize_yaml_rejected():
    from backend.pipeline.exceptions import PipelineYAMLTooLargeError
    big = "pipeline:\n  name: p\n  steps: []\n" + "# pad\n" * 100_000
    assert len(big) > 256_000
    with pytest.raises(PipelineYAMLTooLargeError):
        PipelineParser().parse(big)


# --- M-145: versioning edges ---------------------------------------------------
def test_m145_diff_pipelines_survives_ragged_steps():
    from backend.pipeline.versioning import diff_pipelines

    a = _yaml({"pipeline": {"name": "p", "steps": [
        {"name": "x", "type": "load"},
        "not-a-dict",
        {"type": "load"},            # missing name
        {True: "bool-key"},          # YAML 1.1 `on:`-style key
    ]}})
    b = _yaml({"pipeline": {"name": "p", "steps": [
        {"name": "x", "type": "load", "file_id": "f"},
    ]}})
    diff = diff_pipelines(a, b, 1, 2)
    assert diff.has_changes
    assert "x" in [s.step_name for s in diff.steps_modified]


# --- M-112: engine semantic pins owned by this cluster -------------------------
def test_m112_sort_false_and_fill_nulls_strategy_pinned():
    # YAML `order: false` / `by: false` must be 400s with step context.
    raw = {"pipeline": {"name": "p", "steps": [
        {"name": "load", "type": "load", "file_id": "f1"},
        {"name": "srt", "type": "sort", "input": "load",
         "by": False, "order": False},
    ]}}
    config = PipelineParser().parse(_yaml(raw))
    result = PipelineParser().validate(config, {"f1"})
    assert not result.is_valid
    fields = {e.field for e in result.errors if e.step_name == "srt"}
    assert {"by", "order"} <= fields

    # Every fill_nulls strategy the batch executor implements validates;
    # anything else is rejected (it silently no-ops in the executor).
    from backend.pipeline.parser import VALID_FILL_NULLS_STRATEGIES
    assert VALID_FILL_NULLS_STRATEGIES == {
        "constant", "forward_fill", "backward_fill",
        "mean", "median", "mode",
    }
    for strategy in VALID_FILL_NULLS_STRATEGIES:
        raw = {"pipeline": {"name": "p", "steps": [
            {"name": "load", "type": "load", "file_id": "f1"},
            {"name": "f", "type": "fill_nulls", "input": "load",
             "strategy": strategy, "columns": ["c"],
             **({"constant_value": 0} if strategy == "constant" else {})},
            {"name": "out", "type": "save", "input": "f",
             "filename": "o.csv"},
        ]}}
        config = PipelineParser().parse(_yaml(raw))
        result = PipelineParser().validate(config, {"f1"})
        assert result.is_valid, [
            f"{e.step_name}:{e.field}:{e.message}" for e in result.errors]
