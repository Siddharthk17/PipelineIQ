"""Regression tests for re-audit data-layer findings (R-D-002, R-D-003).

R-D-002: list-valued aggregate functions must fail validation with a 400,
never raise TypeError (unhashable list) out of the validator.
R-D-003: nested-quantifier contract patterns must be rejected at validation
(fail-closed), never evaluated with catastrophic backtracking.
"""
import time

import pyarrow as pa

from backend.contracts.validator import (
    _has_nested_quantifier,
    validate_against_contract,
)


def _parse_and_validate(yaml_text):
    from backend.pipeline.parser import PipelineParser

    parser = PipelineParser()
    parsed = parser.parse(yaml_text)
    return parser.validate(parsed, set())


def test_list_valued_function_is_validation_error_not_crash():
    yaml_text = """pipeline:
  name: t
  steps:
    - name: a
      type: aggregate
      group_by: [g]
      aggregations:
        - column: age
          function: [sum, mean]
    - name: s
      type: save
      input: a
      filename: out.csv
"""
    result = _parse_and_validate(yaml_text)
    assert result.errors, "list-valued function must produce a validation error"
    assert any("function" in e.field for e in result.errors)


def test_nested_quantifier_patterns_rejected():
    assert _has_nested_quantifier("(a+)+")
    assert _has_nested_quantifier("(a+)+$")
    assert _has_nested_quantifier("(a{2,3})+")
    assert not _has_nested_quantifier("a+")
    assert not _has_nested_quantifier("(ab|cd)*e")
    assert not _has_nested_quantifier("\\(a+\\)")
    assert not _has_nested_quantifier("[a+]+x")


def _pattern_contract(pattern: str) -> str:
    return f"""columns:
  c:
    type: string
    nullable: true
    pattern: '{pattern}'
"""


def test_evil_pattern_fails_fast_not_slow():
    table = pa.table({"c": pa.array(["a" * 26 + "b"], type=pa.string())})
    start = time.monotonic()
    result = validate_against_contract(table, _pattern_contract("(a+)+$"))
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"pattern evaluation took {elapsed:.1f}s"
    assert not result.passed
    assert any(
        v.rule in ("invalid_regex", "pattern_mismatch")
        for v in result.violations
    )


def test_normal_patterns_still_work():
    table = pa.table({"c": pa.array(["abc", "def"], type=pa.string())})
    assert validate_against_contract(table, _pattern_contract("^[a-z]+$")).passed
    bad = pa.table({"c": pa.array(["abc", "ABC"], type=pa.string())})
    result = validate_against_contract(bad, _pattern_contract("^[a-z]+$"))
    assert not result.passed
