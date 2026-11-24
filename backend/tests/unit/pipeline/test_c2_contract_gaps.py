"""C2-001/002/003: contract gaps that silently passed (Medium)."""
import pandas as pd

from backend.pipeline.contracts import ColumnContract, DtypeKind


class TestC2001RegexEvaluated:
    def test_matching_values_pass(self):
        c = ColumnContract(name="email", regex=r"^[^@]+@[^@]+$")
        assert c.validate(pd.Series(["a@b.com", "x@y.org"])) == []

    def test_non_matching_values_fail(self):
        c = ColumnContract(name="email", regex=r"^[^@]+@[^@]+$")
        violations = c.validate(pd.Series(["a@b.com", "not-an-email"]))
        assert any(v.rule == "regex" for v in violations)

    def test_invalid_pattern_fails_closed(self):
        c = ColumnContract(name="x", regex="(unclosed")
        violations = c.validate(pd.Series(["anything"]))
        assert any(v.rule == "regex" for v in violations)

    def test_nested_quantifier_rejected(self):
        c = ColumnContract(name="x", regex="(a+)+$")
        violations = c.validate(pd.Series(["aaa"]))
        assert any(v.rule == "regex" for v in violations)


class TestC2002DtypeFailClosed:
    def test_unmapped_dtype_is_violation(self):
        c = ColumnContract(name="n", dtype=DtypeKind.INTEGER)
        # category has no INTEGER mapping: fail closed, not silent pass.
        exotic = pd.Series(pd.Categorical(["a", "b"]))
        violations = c.validate(exotic)
        assert any(v.rule == "dtype" for v in violations)

    def test_known_variants_still_classify(self):
        assert ColumnContract(
            name="n", dtype=DtypeKind.INTEGER).validate(
            pd.Series([1, 2], dtype="int8")) == []
        assert ColumnContract(
            name="f", dtype=DtypeKind.FLOAT).validate(
            pd.Series([1.0], dtype="float32")) == []


class TestC2003BoundsBeyondIUF:
    def test_bool_min_bound_enforced(self):
        c = ColumnContract(name="flag", min_value=5)
        violations = c.validate(pd.Series([True, False]))
        assert any(v.rule == "min_value" for v in violations)

    def test_datetime_max_bound_enforced(self):
        c = ColumnContract(name="ts", max_value="2020-01-01")
        violations = c.validate(
            pd.Series(pd.to_datetime(["2021-06-01", "2019-01-01"])))
        assert any(v.rule == "max_value" for v in violations)

    def test_unsupported_kind_fails_closed(self):
        c = ColumnContract(name="s", min_value=5)
        violations = c.validate(pd.Series(["a", "b"]))
        assert any(v.rule == "min_value" for v in violations)
