"""YAML pipeline configuration parser and validator.

Parses user-defined YAML pipeline configurations into fully typed
dataclass hierarchies, then validates them against a comprehensive
set of rules. Returns structured validation results with actionable
error messages and suggestions.
"""

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Set, Union

import yaml

from backend.config import settings
from backend.pipeline.exceptions import InvalidYAMLError, MissingRequiredFieldError
from backend.utils.string_utils import (
    find_closest_column,
    is_valid_identifier,
    is_safe_filename,
)

logger = logging.getLogger(__name__)


class StepType(str, Enum):
    """Supported pipeline step types."""

    LOAD = "load"
    FILTER = "filter"
    SELECT = "select"
    RENAME = "rename"
    JOIN = "join"
    AGGREGATE = "aggregate"
    SORT = "sort"
    SAVE = "save"
    VALIDATE = "validate"
    PIVOT = "pivot"
    UNPIVOT = "unpivot"
    DEDUPLICATE = "deduplicate"
    FILL_NULLS = "fill_nulls"
    SAMPLE = "sample"


class FilterOperator(str, Enum):
    """Supported filter operators for row-level filtering."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class JoinHow(str, Enum):
    """Supported join types for merging DataFrames."""

    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    OUTER = "outer"


class SortOrder(str, Enum):
    """Sort direction for the sort step."""

    ASC = "asc"
    DESC = "desc"


VALID_AGGREGATION_FUNCTIONS = frozenset(
    {
        "sum",
        "mean",
        "min",
        "max",
        "count",
        "median",
        "std",
        "var",
        "first",
        "last",
    }
)


@dataclass
class StepConfig:
    """Base configuration for all pipeline steps."""

    name: str
    step_type: StepType


@dataclass
class LoadStepConfig(StepConfig):
    """Configuration for a file load step."""

    file_id: str = ""
    alias: Optional[str] = None


@dataclass
class FilterStepConfig(StepConfig):
    """Configuration for a row filter step."""

    input: str = ""
    column: str = ""
    operator: FilterOperator = FilterOperator.EQUALS
    value: Optional[Union[str, int, float]] = None


@dataclass
class SelectStepConfig(StepConfig):
    """Configuration for a column projection (select) step."""

    input: str = ""
    columns: List[str] = field(default_factory=list)


@dataclass
class RenameStepConfig(StepConfig):
    """Configuration for a column rename step."""

    input: str = ""
    mapping: Dict[str, str] = field(default_factory=dict)


@dataclass
class JoinStepConfig(StepConfig):
    """Configuration for a DataFrame join step."""

    left: str = ""
    right: str = ""
    on: str = ""
    how: JoinHow = JoinHow.INNER


@dataclass
class AggregateStepConfig(StepConfig):
    """Configuration for a group-by aggregation step."""

    input: str = ""
    group_by: List[str] = field(default_factory=list)
    aggregations: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class SortStepConfig(StepConfig):
    """Configuration for a row sort step."""

    input: str = ""
    by: str = ""
    order: SortOrder = SortOrder.ASC


@dataclass
class SaveStepConfig(StepConfig):
    """Configuration for saving the output to a file."""

    input: str = ""
    filename: str = ""


@dataclass
class ValidateStepConfig(StepConfig):
    """Configuration for data quality validation step."""

    input: str = ""
    rules: List[dict] = field(default_factory=list)


@dataclass
class PivotStepConfig(StepConfig):
    """Configuration for a pivot step (long to wide)."""

    input: str = ""
    index: List[str] = field(default_factory=list)
    columns: str = ""
    values: str = ""
    aggfunc: str = "sum"
    fill_value: Any = 0


@dataclass
class UnpivotStepConfig(StepConfig):
    """Configuration for an unpivot step (wide to long)."""

    input: str = ""
    id_vars: List[str] = field(default_factory=list)
    value_vars: List[str] = field(default_factory=list)
    var_name: str = "variable"
    value_name: str = "value"


@dataclass
class DeduplicateStepConfig(StepConfig):
    """Configuration for a deduplicate step."""

    input: str = ""
    subset: Optional[List[str]] = None
    keep: str = "first"


@dataclass
class FillNullsStepConfig(StepConfig):
    """Configuration for a fill nulls step."""

    input: str = ""
    strategy: str = "constant"
    columns: List[str] = field(default_factory=list)
    constant_value: Any = None


@dataclass
class SampleStepConfig(StepConfig):
    """Configuration for a sample step."""

    input: str = ""
    n: Optional[int] = None
    fraction: Optional[float] = None
    random_state: int = 42
    stratify_by: Optional[str] = None


@dataclass
class PipelineConfig:
    """Fully typed pipeline configuration parsed from YAML."""

    name: str
    description: Optional[str]
    steps: List[StepConfig]


@dataclass
class ValidationError:
    """A single validation error found during pipeline config validation."""

    step_name: Optional[str]
    field: str
    message: str
    suggestion: Optional[str] = None


@dataclass
class ValidationWarning:
    """A non-blocking warning about the pipeline configuration."""

    step_name: Optional[str]
    message: str


@dataclass
class ValidationResult:
    """Aggregated result of pipeline configuration validation."""

    is_valid: bool
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationWarning] = field(default_factory=list)


_STEP_CONFIG_MAP: Dict[StepType, type] = {
    StepType.LOAD: LoadStepConfig,
    StepType.FILTER: FilterStepConfig,
    StepType.SELECT: SelectStepConfig,
    StepType.RENAME: RenameStepConfig,
    StepType.JOIN: JoinStepConfig,
    StepType.AGGREGATE: AggregateStepConfig,
    StepType.SORT: SortStepConfig,
    StepType.SAVE: SaveStepConfig,
    StepType.VALIDATE: ValidateStepConfig,
    StepType.PIVOT: PivotStepConfig,
    StepType.UNPIVOT: UnpivotStepConfig,
    StepType.DEDUPLICATE: DeduplicateStepConfig,
    StepType.FILL_NULLS: FillNullsStepConfig,
    StepType.SAMPLE: SampleStepConfig,
}


class PipelineParser:
    """Parses YAML pipeline configurations into typed PipelineConfig objects.

    The parser is stateless and safe to reuse across requests. Parsing
    converts raw YAML into typed dataclasses; validation checks semantic
    correctness against registered files and pipeline constraints.
    """

    def parse(self, yaml_string: str) -> PipelineConfig:
        """Parse a YAML string into a typed PipelineConfig.

        Raises:
            InvalidYAMLError: If the YAML cannot be parsed.
            MissingRequiredFieldError: If top-level required fields are missing.
        """
        raw = self._parse_yaml(yaml_string)
        return self._build_pipeline_config(raw)

    def validate(
        self,
        config: PipelineConfig,
        registered_file_ids: Set[str],
    ) -> ValidationResult:
        """Run all validation checks on a parsed pipeline configuration.

        Returns a ValidationResult with errors and warnings rather than raising,
        so all issues can be reported at once.
        """
        errors: List[ValidationError] = []
        warnings: List[ValidationWarning] = []

        self._check_pipeline_name(config, errors)
        self._check_has_steps(config, errors)
        self._check_step_count_limit(config, errors)
        self._check_duplicate_step_names(config, errors)
        self._check_step_name_format(config, errors)
        self._check_step_types(config, errors)
        self._check_step_references(config, errors)
        self._check_load_file_ids(config, registered_file_ids, errors)
        self._check_filter_operators(config, errors)
        self._check_join_configs(config, errors, warnings)
        self._check_aggregate_configs(config, errors)
        self._check_has_save_step(config, errors, warnings)
        self._check_save_filenames(config, errors)
        self._check_validate_rules(config, errors)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _parse_yaml(self, yaml_string: str) -> dict:
        """Parse raw YAML string into a dictionary."""
        try:
            raw = yaml.safe_load(yaml_string)
        except yaml.YAMLError as exc:
            line = getattr(exc, "problem_mark", None)
            line_num = line.line + 1 if line else None
            raise InvalidYAMLError(str(exc), line=line_num) from exc

        if not isinstance(raw, dict):
            raise InvalidYAMLError("YAML root must be a mapping, not a scalar or list")
        return raw

    def _build_pipeline_config(self, raw: dict) -> PipelineConfig:
        """Build a PipelineConfig from parsed YAML dictionary."""
        pipeline_raw = raw.get("pipeline", raw)
        if not isinstance(pipeline_raw, dict):
            raise MissingRequiredFieldError(
                field="pipeline",
                context="Top-level 'pipeline' key must be a mapping",
            )

        name = pipeline_raw.get("name", "")
        if not name:
            raise MissingRequiredFieldError(
                field="name",
                context="Pipeline must have a non-empty 'name' field",
            )

        description = pipeline_raw.get("description")
        raw_steps = pipeline_raw.get("steps", [])

        if not isinstance(raw_steps, list):
            raise MissingRequiredFieldError(
                field="steps",
                context="Pipeline 'steps' must be a list",
            )

        steps = [self._parse_step(step_raw) for step_raw in raw_steps]

        return PipelineConfig(
            name=name,
            description=description,
            steps=steps,
        )

    def _parse_step(self, step_raw: dict) -> StepConfig:
        """Parse a single step dictionary into a typed StepConfig subclass."""
        if not isinstance(step_raw, dict):
            raise MissingRequiredFieldError(
                field="step",
                context="Each step must be a mapping with 'name' and 'type' fields",
            )

        step_name = step_raw.get("name", "")
        step_type_str = step_raw.get("type", "")

        try:
            step_type = StepType(step_type_str)
        except ValueError:
            # Return a base StepConfig so validation can report the invalid type
            return StepConfig(name=step_name, step_type=step_type_str)  # type: ignore[arg-type]

        return self._build_typed_step(step_name, step_type, step_raw)

    def _build_typed_step(
        self, name: str, step_type: StepType, raw: dict
    ) -> StepConfig:
        """Build a step-type-specific config from raw YAML data."""
        if step_type == StepType.LOAD:
            return LoadStepConfig(
                name=name,
                step_type=step_type,
                file_id=raw.get("file_id", ""),
                alias=raw.get("alias"),
            )
        if step_type == StepType.FILTER:
            return self._build_filter_step(name, step_type, raw)
        if step_type == StepType.SELECT:
            return SelectStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                columns=raw.get("columns", []),
            )
        if step_type == StepType.RENAME:
            return RenameStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                mapping=raw.get("mapping", {}),
            )
        if step_type == StepType.JOIN:
            return self._build_join_step(name, step_type, raw)
        if step_type == StepType.AGGREGATE:
            raw_aggs = raw.get("aggregations", [])
            # Convert {column: function} dict to [{column, function}] list
            if isinstance(raw_aggs, dict):
                raw_aggs = [
                    {"column": col, "function": func} for col, func in raw_aggs.items()
                ]
            return AggregateStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                group_by=raw.get("group_by", []),
                aggregations=raw_aggs,
            )
        if step_type == StepType.SORT:
            return self._build_sort_step(name, step_type, raw)
        if step_type == StepType.SAVE:
            return SaveStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                filename=raw.get("filename", ""),
            )
        if step_type == StepType.VALIDATE:
            return ValidateStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                rules=raw.get("rules", []),
            )
        if step_type == StepType.PIVOT:
            index_raw = raw.get("index", "")
            # Handle both string and list for index
            index_list = [index_raw] if isinstance(index_raw, str) else index_raw
            return PivotStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                index=index_list,
                columns=raw.get("columns", ""),
                values=raw.get("values", ""),
                aggfunc=raw.get("aggfunc", "sum"),
                fill_value=raw.get("fill_value", 0),
            )
        if step_type == StepType.UNPIVOT:
            return UnpivotStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                id_vars=raw.get("id_vars", []),
                value_vars=raw.get("value_vars", []),
                var_name=raw.get("var_name", "variable"),
                value_name=raw.get("value_name", "value"),
            )
        if step_type == StepType.DEDUPLICATE:
            return DeduplicateStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                subset=raw.get("subset", []),
                keep=raw.get("keep", "first"),
            )
        if step_type == StepType.FILL_NULLS:
            return FillNullsStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                strategy=raw.get(
                    "method", "constant"
                ),  # method in YAML maps to strategy
                constant_value=raw.get(
                    "value", 0
                ),  # value in YAML maps to constant_value
                columns=raw.get("columns", []),
            )
        if step_type == StepType.SAMPLE:
            return SampleStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                n=raw.get("n"),
                fraction=raw.get("fraction"),
                random_state=raw.get("random_state", 42),
                stratify_by=raw.get("stratify_by"),
            )
        # Unreachable, but satisfies type checkers
        return StepConfig(name=name, step_type=step_type)

    def _build_filter_step(
        self, name: str, step_type: StepType, raw: dict
    ) -> FilterStepConfig:
        """Build a FilterStepConfig, safely parsing the operator."""
        operator_str = raw.get("operator", "equals")
        try:
            operator = FilterOperator(operator_str)
        except ValueError:
            operator = operator_str  # type: ignore[assignment]

        return FilterStepConfig(
            name=name,
            step_type=step_type,
            input=raw.get("input", ""),
            column=raw.get("column", ""),
            operator=operator,
            value=raw.get("value"),
        )

    def _build_join_step(
        self, name: str, step_type: StepType, raw: dict
    ) -> JoinStepConfig:
        """Build a JoinStepConfig, safely parsing the join method."""
        how_str = raw.get("how", "inner")
        try:
            how = JoinHow(how_str)
        except ValueError:
            how = how_str  # type: ignore[assignment]

        # YAML parses bare 'on' as boolean True; check both keys
        on_value = raw.get("on", "") or raw.get(True, "")

        return JoinStepConfig(
            name=name,
            step_type=step_type,
            left=raw.get("left", ""),
            right=raw.get("right", ""),
            on=str(on_value) if on_value else "",
            how=how,
        )

    def _build_sort_step(
        self, name: str, step_type: StepType, raw: dict
    ) -> SortStepConfig:
        """Build a SortStepConfig, safely parsing the sort order."""
        order_str = raw.get("order", "asc")
        try:
            order = SortOrder(order_str)
        except ValueError:
            order = order_str  # type: ignore[assignment]

        return SortStepConfig(
            name=name,
            step_type=step_type,
            input=raw.get("input", ""),
            by=raw.get("by", ""),
            order=order,
        )

    def _check_pipeline_name(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 1: Pipeline name is present, non-empty, no whitespace-only."""
        if not config.name or not config.name.strip():
            errors.append(
                ValidationError(
                    step_name=None,
                    field="name",
                    message="Pipeline name must be a non-empty string",
                )
            )

    def _check_has_steps(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 2: At least one step exists."""
        if not config.steps:
            errors.append(
                ValidationError(
                    step_name=None,
                    field="steps",
                    message="Pipeline must contain at least one step",
                )
            )

    def _check_step_count_limit(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 13: Step count does not exceed MAX_PIPELINE_STEPS."""
        if len(config.steps) > settings.MAX_PIPELINE_STEPS:
            errors.append(
                ValidationError(
                    step_name=None,
                    field="steps",
                    message=(
                        f"Pipeline has {len(config.steps)} steps, "
                        f"exceeding the limit of {settings.MAX_PIPELINE_STEPS}"
                    ),
                )
            )

    def _check_duplicate_step_names(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 3: No duplicate step names."""
        seen: Dict[str, int] = {}
        for step in config.steps:
            seen[step.name] = seen.get(step.name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                errors.append(
                    ValidationError(
                        step_name=name,
                        field="name",
                        message=f"Step name '{name}' is used {count} times. Names must be unique.",
                    )
                )

    def _check_step_name_format(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 12: Step names only contain alphanumeric characters and underscores."""
        for step in config.steps:
            if step.name and not is_valid_identifier(step.name):
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="name",
                        message=(
                            f"Step name '{step.name}' contains invalid characters. "
                            f"Only letters, digits, and underscores are allowed."
                        ),
                    )
                )

    def _check_step_types(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 4: Every step has a valid type."""
        valid_types = [t.value for t in StepType]
        for step in config.steps:
            if not isinstance(step.step_type, StepType):
                suggestion = find_closest_column(str(step.step_type), valid_types)
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="type",
                        message=f"Invalid step type '{step.step_type}'. Valid types: {valid_types}",
                        suggestion=suggestion,
                    )
                )

    def _check_step_references(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 5: All input/left/right references point to earlier steps."""
        seen_steps: List[str] = []
        for step in config.steps:
            self._validate_step_reference(step, seen_steps, errors)
            seen_steps.append(step.name)

    def _validate_step_reference(
        self,
        step: StepConfig,
        available_steps: List[str],
        errors: List[ValidationError],
    ) -> None:
        """Validate all reference fields on a single step config."""
        refs_to_check: List[tuple] = []

        if isinstance(
            step,
            (
                FilterStepConfig,
                SelectStepConfig,
                RenameStepConfig,
                AggregateStepConfig,
                SortStepConfig,
                SaveStepConfig,
            ),
        ):
            refs_to_check.append(("input", step.input))
        elif isinstance(step, JoinStepConfig):
            refs_to_check.append(("left", step.left))
            refs_to_check.append(("right", step.right))

        for field_name, ref_value in refs_to_check:
            if ref_value and ref_value not in available_steps:
                suggestion = find_closest_column(ref_value, available_steps)
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field=field_name,
                        message=(
                            f"References '{ref_value}' which does not exist "
                            f"or comes after this step. Available: {available_steps}"
                        ),
                        suggestion=suggestion,
                    )
                )

    def _check_load_file_ids(
        self,
        config: PipelineConfig,
        registered_file_ids: Set[str],
        errors: List[ValidationError],
    ) -> None:
        """Check 6: Every load step's file_id exists in registered_file_ids."""
        for step in config.steps:
            if isinstance(step, LoadStepConfig):
                if not step.file_id:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="file_id",
                            message="Load step must specify a file_id",
                        )
                    )
                elif step.file_id not in registered_file_ids:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="file_id",
                            message=(
                                f"file_id '{step.file_id}' is not registered. "
                                f"Registered IDs: {sorted(registered_file_ids)}"
                            ),
                        )
                    )

    def _check_filter_operators(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check 7: Every filter step has a valid operator."""
        valid_ops = [op.value for op in FilterOperator]
        for step in config.steps:
            if isinstance(step, FilterStepConfig):
                if not isinstance(step.operator, FilterOperator):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="operator",
                            message=(
                                f"Invalid operator '{step.operator}'. "
                                f"Valid operators: {valid_ops}"
                            ),
                        )
                    )

    def _check_join_configs(
        self,
        config: PipelineConfig,
        errors: List[ValidationError],
        warnings: List[ValidationWarning],
    ) -> None:
        """Check 8: Every join step specifies a valid 'how' method."""
        valid_hows = [h.value for h in JoinHow]
        for step in config.steps:
            if isinstance(step, JoinStepConfig):
                if not step.on:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="on",
                            message="Join step must specify a join key via 'on' field",
                        )
                    )
                if not isinstance(step.how, JoinHow):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="how",
                            message=(
                                f"Invalid join method '{step.how}'. "
                                f"Valid methods: {valid_hows}"
                            ),
                        )
                    )

    def _check_aggregate_configs(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Checks 9 & 10: Aggregation step has aggregations with valid functions."""
        for step in config.steps:
            if isinstance(step, AggregateStepConfig):
                if not step.aggregations:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="aggregations",
                            message="Aggregate step must define at least one aggregation",
                        )
                    )
                for agg in step.aggregations:
                    if isinstance(agg, dict):
                        func_name = agg.get("function", "")
                    else:
                        func_name = str(agg)
                    if func_name not in VALID_AGGREGATION_FUNCTIONS:
                        errors.append(
                            ValidationError(
                                step_name=step.name,
                                field="aggregations.function",
                                message=(
                                    f"Invalid aggregation function '{func_name}'. "
                                    f"Valid functions: {sorted(VALID_AGGREGATION_FUNCTIONS)}"
                                ),
                            )
                        )

    def _check_has_save_step(
        self,
        config: PipelineConfig,
        errors: List[ValidationError],
        warnings: List[ValidationWarning],
    ) -> None:
        """Check 11: At least one save step exists."""
        has_save = any(isinstance(step, SaveStepConfig) for step in config.steps)
        if not has_save:
            warnings.append(
                ValidationWarning(
                    step_name=None,
                    message=(
                        "Pipeline has no 'save' step. Results will be computed "
                        "but not persisted to an output file."
                    ),
                )
            )

    def _check_save_filenames(
        self, config: PipelineConfig, errors: List[ValidationError]
    ) -> None:
        """Check that all save step filenames are safe and don't allow path traversal."""
        for step in config.steps:
            if isinstance(step, SaveStepConfig):
                if not step.filename:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="filename",
                            message="Save step must specify a filename",
                        )
                    )
                elif not is_safe_filename(step.filename):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="filename",
                            message=f"Invalid filename '{step.filename}'. Path traversal characters are forbidden.",
                        )
                    )

    def _check_validate_rules(
        self,
        config: PipelineConfig,
        errors: List[ValidationError],
    ) -> None:
        """Check 12: Validate step rules use supported check types."""
        from backend.pipeline.validators import SUPPORTED_CHECKS

        for step in config.steps:
            if not isinstance(step, ValidateStepConfig):
                continue
            for rule in step.rules:
                check = rule.get("check", "")
                if check not in SUPPORTED_CHECKS:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="rules",
                            message=(
                                f"Unknown check type '{check}'. "
                                f"Supported: {', '.join(sorted(SUPPORTED_CHECKS))}"
                            ),
                        )
                    )
