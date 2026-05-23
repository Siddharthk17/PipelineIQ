"""Unit tests for the post-execution contract validation engine.

Tests the ``backend.contracts.validator`` module in isolation — no database,
no pipeline execution, just Arrow tables against YAML contract definitions.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from backend.contracts.validator import (
    ContractViolation,
    ContractValidationResult,
    TYPE_CATEGORY_MAP,
    _arrow_type_category,
    _parse_contract,
    validate_against_contract,
)


_SAMPLE_CONTRACT = """
columns:
  order_id:
    type: integer
    nullable: false
    unique: true
  amount:
    type: float
    nullable: false
    min_value: 0
    max_value: 10000
  status:
    type: string
    nullable: false
    allowed_values:
      - delivered
      - pending
      - shipped
      - cancelled
  region:
    type: string
    nullable: true
  customer_id:
    type: integer
    nullable: false
min_rows: 1
max_rows: 1000
null_thresholds:
  region: 50
"""


class TestParseContract:
    """YAML parsing edge cases."""

    def test_valid_yaml_parses(self):
        data = _parse_contract(_SAMPLE_CONTRACT)
        assert "columns" in data
        assert "order_id" in data["columns"]

    def test_empty_yaml_returns_empty_dict(self):
        assert _parse_contract("") == {}

    def test_garbage_yaml_returns_empty_dict(self):
        assert _parse_contract("{{broken: yaml:::") == {}

    def test_non_dict_yaml_returns_empty_dict(self):
        assert _parse_contract("12345") == {}


class TestArrowTypeCategory:
    """PyArrow type-to-category mapping."""

    def test_integer_types(self):
        for t in (pa.int8(), pa.int16(), pa.int32(), pa.int64(),
                  pa.uint8(), pa.uint16(), pa.uint32(), pa.uint64()):
            assert _arrow_type_category(t) == "integer", f"{t} should be integer"

    def test_float_types(self):
        for t in (pa.float16(), pa.float32(), pa.float64()):
            assert _arrow_type_category(t) == "float", f"{t} should be float"

    def test_string_types(self):
        for t in (pa.string(), pa.large_string()):
            assert _arrow_type_category(t) == "string", f"{t} should be string"

    def test_timestamp_types(self):
        for unit in ("s", "ms", "us", "ns"):
            assert _arrow_type_category(pa.timestamp(unit)) == "datetime"

    def test_date_type(self):
        assert _arrow_type_category(pa.date32()) == "datetime"
        assert _arrow_type_category(pa.date64()) == "datetime"

    def test_boolean_type(self):
        assert _arrow_type_category(pa.bool_()) == "boolean"

    def test_unknown_type(self):
        assert _arrow_type_category(pa.null()) == "null"


class TestValidateAgainstContract:
    """Comprehensive validation scenarios."""

    def test_valid_data_passes(self):
        table = pa.table({
            "order_id": pa.array([1, 2, 3], type=pa.int64()),
            "amount": pa.array([100.0, 200.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert result.passed

    def test_missing_column_fails(self):
        table = pa.table({
            "order_id": pa.array([1, 2], type=pa.int64()),
            "amount": pa.array([100.0, 200.0], type=pa.float64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert not result.passed
        assert any(v.rule == "column_removed" for v in result.violations)

    def test_type_mismatch_fails(self):
        table = pa.table({
            "order_id": pa.array(["a", "b"], type=pa.string()),
            "amount": pa.array([100.0, 200.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending"]),
            "region": pa.array(["US", "EU"]),
            "customer_id": pa.array([10, 20], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert not result.passed
        assert any(v.rule == "type_changed" and v.column == "order_id" for v in result.violations)

    def test_null_not_allowed_fails(self):
        table = pa.table({
            "order_id": pa.array([1, None, 3], type=pa.int64()),
            "amount": pa.array([100.0, 200.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "not_null" and v.column == "order_id" for v in result.violations)

    def test_duplicate_unique_fails(self):
        table = pa.table({
            "order_id": pa.array([1, 1, 3], type=pa.int64()),
            "amount": pa.array([100.0, 200.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "unique" for v in result.violations)

    def test_min_value_violation(self):
        table = pa.table({
            "order_id": pa.array([1, 2, 3], type=pa.int64()),
            "amount": pa.array([-50.0, 200.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "min_value" for v in result.violations)

    def test_max_value_violation(self):
        table = pa.table({
            "order_id": pa.array([1, 2, 3], type=pa.int64()),
            "amount": pa.array([100.0, 99999.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "max_value" for v in result.violations)

    def test_allowed_values_violation(self):
        table = pa.table({
            "order_id": pa.array([1, 2, 3], type=pa.int64()),
            "amount": pa.array([100.0, 200.0, 300.0], type=pa.float64()),
            "status": pa.array(["delivered", "INVALID", "shipped"]),
            "region": pa.array(["US", "EU", "US"]),
            "customer_id": pa.array([10, 20, 30], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "allowed_values" for v in result.violations)

    def test_row_count_below_minimum(self):
        table = pa.table({
            "order_id": pa.array([], type=pa.int64()),
            "amount": pa.array([], type=pa.float64()),
            "status": pa.array([], type=pa.string()),
            "region": pa.array([], type=pa.string()),
            "customer_id": pa.array([], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "row_count_below_minimum" for v in result.violations)

    def test_null_threshold_exceeded(self):
        table = pa.table({
            "order_id": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "amount": pa.array([100.0, 200.0, 300.0, 400.0, 500.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending", "shipped", "delivered", "pending"]),
            "region": pa.array([None, None, None, None, "US"]),
            "customer_id": pa.array([10, 20, 30, 40, 50], type=pa.int64()),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        assert any(v.rule == "null_threshold_exceeded" for v in result.violations)

    def test_empty_contract_returns_error(self):
        table = pa.table({"a": [1]})
        result = validate_against_contract(table, "")
        assert any(v.rule == "parse_error" for v in result.violations)

    def test_none_table_returns_no_output_error(self):
        result = validate_against_contract(None, _SAMPLE_CONTRACT)
        assert any(v.rule == "no_output" for v in result.violations)

    def test_unexpected_columns_warn(self):
        table = pa.table({
            "order_id": pa.array([1, 2], type=pa.int64()),
            "amount": pa.array([100.0, 200.0], type=pa.float64()),
            "status": pa.array(["delivered", "pending"]),
            "region": pa.array(["US", "EU"]),
            "customer_id": pa.array([10, 20], type=pa.int64()),
            "extra_column": pa.array(["x", "y"]),
        })
        result = validate_against_contract(table, _SAMPLE_CONTRACT)
        unexpected = [v for v in result.violations if v.rule == "unexpected_column"]
        assert len(unexpected) > 0
        assert all(v.severity == "warning" for v in unexpected)


class TestDataclassTypes:
    """ContractViolation and ContractValidationResult shape."""

    def test_contract_violation_fields(self):
        v = ContractViolation(column="c", rule="r", severity="error", message="m")
        assert v.column == "c"
        assert v.rule == "r"
        assert v.severity == "error"
        assert v.message == "m"
        assert v.actual is None
        assert v.expected is None

    def test_contract_violation_with_optional(self):
        v = ContractViolation(column="c", rule="r", severity="error", message="m",
                              actual="a", expected="e")
        assert v.actual == "a"
        assert v.expected == "e"

    def test_validation_result_defaults(self):
        r = ContractValidationResult(passed=True)
        assert r.passed is True
        assert r.violations == []

    def test_validation_result_with_violations(self):
        v = ContractViolation(column="c", rule="r", severity="error", message="m")
        r = ContractValidationResult(passed=False, violations=[v])
        assert r.passed is False
        assert len(r.violations) == 1


class TestBreachReportBlockingLogic:
    """Verify build_breach_report respects violation severity for blocking decisions."""

    def test_unexpected_column_only_never_blocks_even_at_block_severity(self):
        """unexpected_column violations must never block, even at severity=block."""
        from backend.contracts.validator import build_breach_report
        violations = [
            ContractViolation(column="extra", rule="unexpected_column", severity="warning", message="not in contract"),
        ]
        val = ContractValidationResult(passed=False, violations=violations)
        report = build_breach_report(val, severity="block")
        assert report.has_breaches is True
        assert report.should_block_run is False, (
            "unexpected_column must never block the run, even at severity=block"
        )

    def test_error_violation_blocks_at_block_severity(self):
        """error-severity violations block when contract severity=block."""
        from backend.contracts.validator import build_breach_report
        violations = [
            ContractViolation(column="revenue", rule="column_removed", severity="error", message="missing"),
        ]
        val = ContractValidationResult(passed=False, violations=violations)
        report = build_breach_report(val, severity="block")
        assert report.should_block_run is True

    def test_error_violation_does_not_block_at_warn_severity(self):
        """error-severity violations do NOT block when contract severity=warn."""
        from backend.contracts.validator import build_breach_report
        violations = [
            ContractViolation(column="revenue", rule="column_removed", severity="error", message="missing"),
        ]
        val = ContractValidationResult(passed=False, violations=violations)
        report = build_breach_report(val, severity="warn")
        assert report.should_block_run is False

    def test_null_threshold_warning_does_not_block_at_block_severity(self):
        """null_threshold_exceeded (severity=warning) must not block even at severity=block."""
        from backend.contracts.validator import build_breach_report
        violations = [
            ContractViolation(column="amount", rule="null_threshold_exceeded", severity="warning", message="nulls at 40%"),
        ]
        val = ContractValidationResult(passed=False, violations=violations)
        report = build_breach_report(val, severity="block")
        assert report.should_block_run is False, (
            "null_threshold_exceeded is severity=warning and must not block"
        )

    def test_mixed_error_and_warning_blocks_at_block_severity(self):
        """混合的 error+warning violations block when contract severity=block."""
        from backend.contracts.validator import build_breach_report
        violations = [
            ContractViolation(column="revenue", rule="column_removed", severity="error", message="missing"),
            ContractViolation(column="extra", rule="unexpected_column", severity="warning", message="extra"),
        ]
        val = ContractValidationResult(passed=False, violations=violations)
        report = build_breach_report(val, severity="block")
        assert report.should_block_run is True
