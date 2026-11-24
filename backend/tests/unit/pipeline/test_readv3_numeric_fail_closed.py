"""Regression tests for readv3-correctness B2.

B2 (Medium): ``greater_than``/``less_than``/``between`` silently PASSED
non-numeric strings — pd.to_numeric(coerce) maps garbage to NaN and NaN
comparisons are False, so garbage rows were never counted as failing.
Unparseable non-null values must now count as violations (fail closed).
"""

import pandas as pd

from backend.pipeline.validators import execute_validate


def _run(df, check, **params):
    rule = {"column": "x", "check": check, "severity": "error", **params}
    return execute_validate(df, [rule], "v").rule_results[0]


def test_b2_greater_than_fails_closed_on_garbage():
    df = pd.DataFrame({"x": ["abc", "def", "25"]})
    res = _run(df, "greater_than", value=10)
    assert res.passed is False
    # "abc" and "def" are unparseable -> violations; "25" passes.
    assert res.failing_count == 2


def test_b2_less_than_fails_closed_on_garbage():
    df = pd.DataFrame({"x": ["abc", "5", "25"]})
    res = _run(df, "less_than", value=10)
    assert res.passed is False
    # "abc" unparseable + "25" genuinely out of range; "5" passes.
    assert res.failing_count == 2


def test_b2_between_fails_closed_on_garbage():
    df = pd.DataFrame({"x": ["abc", "def", "25"]})
    res = _run(df, "between", min=0, max=100)
    assert res.passed is False
    assert res.failing_count == 2


def test_b2_clean_numeric_data_still_passes():
    df = pd.DataFrame({"x": [11, 20, 30]})
    assert _run(df, "greater_than", value=10).passed is True
    assert _run(df, "between", min=0, max=100).passed is True
    assert _run(df, "less_than", value=100).passed is True


def test_b2_nulls_still_owned_by_not_null_not_numeric_gates():
    # Genuine NULLs keep the old pass-through; not_null owns them.
    df = pd.DataFrame({"x": [None, 25.0]})
    assert _run(df, "greater_than", value=10).passed is True
    assert _run(df, "between", min=0, max=100).passed is True
    assert _run(df, "less_than", value=100).passed is True
