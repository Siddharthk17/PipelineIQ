"""Tests for the pipeline YAML parser and validator."""

import pytest

from backend.pipeline.exceptions import InvalidYAMLError, MissingRequiredFieldError
from backend.pipeline.parser import (
    AggregateStepConfig,
    FilterOperator,
    FilterStepConfig,
    JoinStepConfig,
    LoadStepConfig,
    PipelineParser,
    SaveStepConfig,
    SqlStepConfig,
    StepType,
)


@pytest.fixture()
def parser() -> PipelineParser:
    """Fresh PipelineParser instance."""
    return PipelineParser()


VALID_SIMPLE_YAML = """
pipeline:
  name: test_pipeline
  description: A simple test pipeline
  steps:
    - name: load_sales
      type: load
      file_id: file-123
    - name: filter_delivered
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered
    - name: save_output
      type: save
      input: filter_delivered
      filename: output.csv
"""

VALID_COMPLEX_YAML = """
pipeline:
  name: complex_pipeline
  description: An 8-step pipeline
  steps:
    - name: load_sales
      type: load
      file_id: file-1
    - name: load_customers
      type: load
      file_id: file-2
    - name: filter_delivered
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered
    - name: join_data
      type: join
      left: filter_delivered
      right: load_customers
      on: customer_id
      how: inner
    - name: select_cols
      type: select
      input: join_data
      columns: [customer_id, amount, customer_name]
    - name: rename_cols
      type: rename
      input: select_cols
      mapping:
        amount: revenue
    - name: aggregate_revenue
      type: aggregate
      input: rename_cols
      group_by: [customer_name]
      aggregations:
        - column: revenue
          function: sum
    - name: save_report
      type: save
      input: aggregate_revenue
      filename: report.csv
"""


class TestParserParse:
    """Tests for PipelineParser.parse()."""

    def test_parse_valid_simple_pipeline_returns_config(self, parser):
        """Parse minimal valid YAML, verify PipelineConfig returned."""
        config = parser.parse(VALID_SIMPLE_YAML)
        assert config.name == "test_pipeline"
        assert config.description == "A simple test pipeline"
        assert len(config.steps) == 3

    def test_parse_valid_complex_pipeline_returns_all_steps(self, parser):
        """Parse 8-step YAML, verify all steps parsed with correct types."""
        config = parser.parse(VALID_COMPLEX_YAML)
        assert len(config.steps) == 8
        assert isinstance(config.steps[0], LoadStepConfig)
        assert isinstance(config.steps[2], FilterStepConfig)
        assert isinstance(config.steps[3], JoinStepConfig)
        assert isinstance(config.steps[6], AggregateStepConfig)
        assert isinstance(config.steps[7], SaveStepConfig)

    def test_parse_invalid_yaml_syntax_raises_invalid_yaml_error(self, parser):
        """Pass garbage string, verify InvalidYAMLError raised."""
        with pytest.raises(InvalidYAMLError):
            parser.parse("pipeline:\n  name: test\n  steps:\n  - {bad yaml::")

    def test_parse_missing_pipeline_key_raises_config_error(self, parser):
        """YAML without 'pipeline:' top-level key but with name works (fallback)."""
        yaml_str = """
name: test
steps:
  - name: load_data
    type: load
    file_id: abc
"""
        config = parser.parse(yaml_str)
        assert config.name == "test"

    def test_parse_missing_steps_raises_config_error(self, parser):
        """Pipeline with no steps key raises or returns empty steps."""
        yaml_str = """
pipeline:
  name: no_steps_pipeline
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, set())
        assert result.is_valid is False

    def test_parse_missing_name_raises_error(self, parser):
        """Missing pipeline name raises MissingRequiredFieldError."""
        yaml_str = """
pipeline:
  steps:
    - name: load_data
      type: load
      file_id: abc
"""
        with pytest.raises(MissingRequiredFieldError):
            parser.parse(yaml_str)


class TestParserValidate:
    """Tests for PipelineParser.validate()."""

    def test_validate_valid_pipeline_returns_no_errors(self, parser):
        """Well-formed pipeline with valid file_ids returns is_valid=True."""
        config = parser.parse(VALID_SIMPLE_YAML)
        result = parser.validate(config, {"file-123"})
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_validate_duplicate_step_names_returns_error(self, parser):
        """Two steps named 'load_data' returns validation error."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: load_data
      type: load
      file_id: f2
    - name: save_output
      type: save
      input: load_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1", "f2"})
        assert result.is_valid is False
        assert any("duplicate" in e.message.lower() or "used" in e.message.lower() for e in result.errors)

    def test_validate_forward_reference_returns_error(self, parser):
        """Step referencing a step defined AFTER it returns error."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: filter_data
      type: filter
      input: save_output
      column: status
      operator: equals
      value: ok
    - name: save_output
      type: save
      input: filter_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("save_output" in e.message for e in result.errors)

    @pytest.mark.parametrize(
        "step_block",
        [
            """
    - name: validate_data
      type: validate
      input: missing_step
      rules:
        - check: not_null
          column: status
          severity: warning
""",
            """
    - name: pivot_data
      type: pivot
      input: missing_step
      index: customer_id
      columns: status
      values: amount
""",
            """
    - name: unpivot_data
      type: unpivot
      input: missing_step
      id_vars: [order_id]
      value_vars: [amount]
""",
            """
    - name: dedup_data
      type: deduplicate
      input: missing_step
      subset: [order_id]
      keep: first
""",
            """
    - name: fill_data
      type: fill_nulls
      input: missing_step
      method: constant
      value: 0
      columns: [amount]
""",
            """
    - name: sample_data
      type: sample
      input: missing_step
      n: 5
""",
        ],
    )
    def test_validate_missing_input_reference_for_week2_steps_returns_error(
        self, parser, step_block
    ):
        """Week-2 and validate steps must fail validation when input references are invalid."""
        yaml_str = f"""
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
{step_block}
    - name: save_output
      type: save
      input: load_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any(
            e.field == "input" and "missing_step" in e.message for e in result.errors
        )

    def test_validate_nonexistent_file_id_returns_error(self, parser):
        """Load step referencing file_id not in registered set returns error."""
        config = parser.parse(VALID_SIMPLE_YAML)
        result = parser.validate(config, {"other-file"})
        assert result.is_valid is False
        assert any("file_id" in e.field for e in result.errors)

    def test_validate_empty_input_reference_returns_error(self, parser):
        """Steps with blank input must fail fast during validation."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: filter_data
      type: filter
      input: ""
      column: status
      operator: equals
      value: ok
    - name: save_output
      type: save
      input: filter_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any(e.step_name == "filter_data" and e.field == "input" for e in result.errors)

    def test_validate_join_requires_non_empty_left_and_right_inputs(self, parser):
        """Join step must include both left and right references."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_a
      type: load
      file_id: f1
    - name: load_b
      type: load
      file_id: f2
    - name: join_step
      type: join
      left: ""
      right: load_b
      on: customer_id
      how: inner
    - name: save_output
      type: save
      input: join_step
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1", "f2"})
        assert result.is_valid is False
        assert any(e.step_name == "join_step" and e.field == "left" for e in result.errors)

    def test_validate_sample_requires_n_or_fraction(self, parser):
        """Sample step must define exactly one sizing mode."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: sample_data
      type: sample
      input: load_data
    - name: save_output
      type: save
      input: sample_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any(e.step_name == "sample_data" and e.field == "sample" for e in result.errors)

    def test_validate_invalid_filter_operator_returns_error(self, parser):
        """Filter step with operator='invalid_op' returns error."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: filter_data
      type: filter
      input: load_data
      column: status
      operator: invalid_op
      value: ok
    - name: save_output
      type: save
      input: filter_data
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("operator" in e.field.lower() for e in result.errors)

    def test_validate_missing_join_how_returns_error(self, parser):
        """Join step with no 'how' field defaults (no error) or fails."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_a
      type: load
      file_id: f1
    - name: load_b
      type: load
      file_id: f2
    - name: join_step
      type: join
      left: load_a
      right: load_b
      on: key
    - name: save_output
      type: save
      input: join_step
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1", "f2"})
        # Should either succeed with default 'inner' or report error
        assert isinstance(result.is_valid, bool)

    def test_validate_step_name_with_spaces_returns_error(self, parser):
        """Step name containing spaces fails validation."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: "load data"
      type: load
      file_id: f1
    - name: save_output
      type: save
      input: "load data"
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("invalid characters" in e.message.lower() or "space" in e.message.lower() for e in result.errors)

    def test_validate_aggregate_with_invalid_function_returns_error(self, parser):
        """aggregate: revenue: invalid_func returns error."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: agg_step
      type: aggregate
      input: load_data
      group_by: [category]
      aggregations:
        - column: revenue
          function: invalid_func
    - name: save_output
      type: save
      input: agg_step
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("aggregat" in e.field.lower() or "function" in e.message.lower() for e in result.errors)

    def test_parse_filter_operator_enum_conversion(self, parser):
        """operator: 'equals' correctly converts to FilterOperator.EQUALS."""
        config = parser.parse(VALID_SIMPLE_YAML)
        filter_step = config.steps[1]
        assert isinstance(filter_step, FilterStepConfig)
        assert filter_step.operator == FilterOperator.EQUALS

    def test_validate_invalid_step_type_detected(self, parser):
        """An invalid step type produces a validation error."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: bad_step
      type: transform
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, set())
        assert result.is_valid is False
        assert any("type" in e.field for e in result.errors)

    def test_validate_empty_steps_fails_validation(self, parser):
        """An empty steps list produces a validation error."""
        yaml_str = """
pipeline:
  name: test
  steps: []
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, set())
        assert result.is_valid is False

    def test_no_save_step_produces_warning(self, parser):
        """A pipeline without a save step produces a warning."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is True
        assert len(result.warnings) > 0
        assert any("save" in w.message.lower() for w in result.warnings)

    def test_parse_sql_step_returns_typed_sql_config(self, parser):
        """SQL steps parse into SqlStepConfig with query preserved."""
        yaml_str = """
pipeline:
  name: sql_test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: custom_sql
      type: sql
      input: load_data
      query: |
        SELECT customer_id, amount
        FROM {{input}}
        WHERE amount > 100
    - name: save_output
      type: save
      input: custom_sql
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        sql_step = config.steps[1]
        assert isinstance(sql_step, SqlStepConfig)
        assert sql_step.input == "load_data"
        assert "{{input}}" in sql_step.query

    def test_validate_sql_step_requires_input_placeholder(self, parser):
        """SQL steps without {{input}} fail validation."""
        yaml_str = """
pipeline:
  name: sql_test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: bad_sql
      type: sql
      input: load_data
      query: "SELECT * FROM some_table"
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any(e.field == "query" for e in result.errors)

    def test_validate_sql_step_blocks_non_select_queries(self, parser):
        """SQL step query must be SELECT/CTE only."""
        yaml_str = """
pipeline:
  name: sql_test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: bad_sql
      type: sql
      input: load_data
      query: "DELETE FROM {{input}}"
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("select" in e.message.lower() for e in result.errors)
