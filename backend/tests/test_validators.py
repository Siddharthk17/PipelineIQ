"""Tests for data quality validation (Deliverable 6).

22 tests covering all 12 check types, severity behavior, and integration.
"""

import pytest
import pandas as pd
from backend.pipeline.validators import execute_validate, _execute_single_rule


@pytest.fixture
def sample_df():
    return pd.DataFrame({
        "order_id": [1, 2, 3, 4, 5],
        "amount": [100.0, 200.0, -50.0, 0.0, 300.0],
        "status": ["delivered", "cancelled", "invalid", "delivered", "pending"],
        "email": ["a@b.com", "bad-email", "c@d.com", "e@f.com", "g@h.com"],
    })


class TestValidationRules:
    """Tests for individual validation check types."""

    def test_not_null_passes_when_no_nulls(self, sample_df):
        rules = [{"column": "order_id", "check": "not_null"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.passed is True
        assert result.rule_results[0].passed is True

    def test_not_null_fails_with_correct_count(self):
        df = pd.DataFrame({"col": [1, None, 3, None, 5]})
        rules = [{"column": "col", "check": "not_null"}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 2

    def test_not_null_returns_failing_examples(self):
        df = pd.DataFrame({"col": [1, None, 3]})
        rules = [{"column": "col", "check": "not_null"}]
        result = execute_validate(df, rules, "test")
        assert len(result.rule_results[0].failing_examples) > 0

    def test_greater_than_passes_all_above(self):
        df = pd.DataFrame({"val": [10, 20, 30]})
        rules = [{"column": "val", "check": "greater_than", "value": 5}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_greater_than_fails_with_zero_and_negative(self, sample_df):
        rules = [{"column": "amount", "check": "greater_than", "value": 0}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 2  # -50 and 0

    def test_in_values_passes_valid(self):
        df = pd.DataFrame({"status": ["a", "b", "c"]})
        rules = [{"column": "status", "check": "in_values", "values": ["a", "b", "c"]}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_in_values_fails_invalid_with_count(self, sample_df):
        rules = [{"column": "status", "check": "in_values",
                  "values": ["delivered", "cancelled", "pending"]}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 1  # "invalid"

    def test_matches_pattern_passes_valid_emails(self):
        df = pd.DataFrame({"email": ["a@b.com", "c@d.org"]})
        rules = [{"column": "email", "check": "matches_pattern",
                  "pattern": r"^[^@]+@[^@]+\.[^@]+$"}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_matches_pattern_fails_invalid_emails(self, sample_df):
        rules = [{"column": "email", "check": "matches_pattern",
                  "pattern": r"^[^@]+@[^@]+\.[^@]+$"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 1  # "bad-email"

    def test_no_duplicates_passes_unique(self, sample_df):
        rules = [{"column": "order_id", "check": "no_duplicates"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_no_duplicates_fails_with_count(self):
        df = pd.DataFrame({"col": [1, 2, 2, 3, 3]})
        rules = [{"column": "col", "check": "no_duplicates"}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 2

    def test_min_rows_passes_above_minimum(self, sample_df):
        rules = [{"check": "min_rows", "value": 3}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_min_rows_fails_below_minimum(self, sample_df):
        rules = [{"check": "min_rows", "value": 100}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False

    def test_positive_passes_all_positive(self):
        df = pd.DataFrame({"val": [1, 2, 3]})
        rules = [{"column": "val", "check": "positive"}]
        result = execute_validate(df, rules, "test")
        assert result.rule_results[0].passed is True

    def test_positive_fails_zero_and_negative(self, sample_df):
        rules = [{"column": "amount", "check": "positive"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert result.rule_results[0].failing_count == 2  # -50 and 0


class TestValidationSeverity:
    """Tests for severity-based pass/fail behavior."""

    def test_error_severity_fails_step(self, sample_df):
        rules = [{"column": "amount", "check": "positive", "severity": "error"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.passed is False
        assert result.error_count == 1

    def test_warning_severity_passes_step(self, sample_df):
        rules = [{"column": "amount", "check": "positive", "severity": "warning"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.passed is True
        assert result.warning_count == 1

    def test_multiple_rules_all_evaluated(self, sample_df):
        rules = [
            {"column": "order_id", "check": "not_null", "severity": "error"},
            {"column": "amount", "check": "positive", "severity": "warning"},
            {"column": "status", "check": "in_values",
             "values": ["delivered", "cancelled", "pending"], "severity": "error"},
        ]
        result = execute_validate(sample_df, rules, "test")
        assert len(result.rule_results) == 3

    def test_output_df_unchanged_on_warning(self, sample_df):
        rules = [{"column": "amount", "check": "positive", "severity": "warning"}]
        result = execute_validate(sample_df, rules, "test")
        pd.testing.assert_frame_equal(result.output_df, sample_df)


class TestValidationEdgeCases:
    """Tests for edge cases and error handling."""

    def test_unknown_check_returns_failure(self, sample_df):
        rules = [{"column": "amount", "check": "unknown_check"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert "Unknown check" in result.rule_results[0].message

    def test_nonexistent_column_returns_failure(self, sample_df):
        rules = [{"column": "nonexistent", "check": "not_null"}]
        result = execute_validate(sample_df, rules, "test")
        assert result.rule_results[0].passed is False
        assert "not found" in result.rule_results[0].message

    def test_validate_step_in_full_pipeline_integration(
        self, client, sales_csv_bytes
    ):
        """Validate step type is accepted in pipeline validation."""
        from backend.tests.conftest import upload_file, build_simple_pipeline_yaml
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = f"""pipeline:
  name: test_validate
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: validate_sales
      type: validate
      input: load_sales
      rules:
        - column: amount
          check: not_null
          severity: error
    - name: save_output
      type: save
      input: validate_sales
      filename: output.csv
"""
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True
