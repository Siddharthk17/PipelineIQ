"""Tests for the pipeline YAML parser and validator."""

# Third-party packages
import pytest

# Internal modules
from backend.pipeline.exceptions import InvalidYAMLError, MissingRequiredFieldError
from backend.pipeline.parser import (
    FilterOperator,
    FilterStepConfig,
    LoadStepConfig,
    PipelineParser,
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


class TestParserParse:
    """Tests for PipelineParser.parse()."""

    def test_parse_valid_yaml_returns_pipeline_config(self, parser):
        """Valid YAML produces a PipelineConfig with correct name and steps."""
        config = parser.parse(VALID_SIMPLE_YAML)
        assert config.name == "test_pipeline"
        assert config.description == "A simple test pipeline"
        assert len(config.steps) == 3

    def test_parse_creates_typed_step_configs(self, parser):
        """Steps are parsed into their specific config subclasses."""
        config = parser.parse(VALID_SIMPLE_YAML)
        assert isinstance(config.steps[0], LoadStepConfig)
        assert isinstance(config.steps[1], FilterStepConfig)

    def test_parse_load_step_has_file_id(self, parser):
        """Load step config contains the file_id from YAML."""
        config = parser.parse(VALID_SIMPLE_YAML)
        load_step = config.steps[0]
        assert isinstance(load_step, LoadStepConfig)
        assert load_step.file_id == "file-123"

    def test_parse_filter_step_has_operator(self, parser):
        """Filter step config contains the parsed operator enum."""
        config = parser.parse(VALID_SIMPLE_YAML)
        filter_step = config.steps[1]
        assert isinstance(filter_step, FilterStepConfig)
        assert filter_step.operator == FilterOperator.EQUALS
        assert filter_step.column == "status"
        assert filter_step.value == "delivered"

    def test_parse_invalid_yaml_raises_error(self, parser):
        """Syntactically invalid YAML raises InvalidYAMLError."""
        with pytest.raises(InvalidYAMLError):
            parser.parse("pipeline:\n  name: test\n  steps:\n  - {bad yaml::")

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

    def test_valid_pipeline_passes_validation(self, parser):
        """A well-formed pipeline passes all validation checks."""
        config = parser.parse(VALID_SIMPLE_YAML)
        result = parser.validate(config, {"file-123"})
        assert result.is_valid is True
        assert len(result.errors) == 0

    def test_duplicate_step_names_detected(self, parser):
        """Duplicate step names produce a validation error."""
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

    def test_invalid_step_type_detected(self, parser):
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

    def test_unregistered_file_id_detected(self, parser):
        """A load step referencing an unregistered file_id fails validation."""
        config = parser.parse(VALID_SIMPLE_YAML)
        result = parser.validate(config, {"other-file"})
        assert result.is_valid is False
        assert any("file_id" in e.field for e in result.errors)

    def test_invalid_step_reference_detected(self, parser):
        """A step referencing a non-existent input fails validation."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: f1
    - name: filter_data
      type: filter
      input: nonexistent_step
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
        assert any("nonexistent_step" in e.message for e in result.errors)

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

    def test_empty_steps_fails_validation(self, parser):
        """An empty steps list produces a validation error."""
        yaml_str = """
pipeline:
  name: test
  steps: []
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, set())
        assert result.is_valid is False

    def test_invalid_step_name_format_detected(self, parser):
        """A step name with special characters fails validation."""
        yaml_str = """
pipeline:
  name: test
  steps:
    - name: load-data!
      type: load
      file_id: f1
    - name: save_output
      type: save
      input: load-data!
      filename: out.csv
"""
        config = parser.parse(yaml_str)
        result = parser.validate(config, {"f1"})
        assert result.is_valid is False
        assert any("invalid characters" in e.message.lower() for e in result.errors)
