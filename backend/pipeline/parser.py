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
from typing import Any, ClassVar

import yaml

from backend.config import settings
from backend.execution.sql_builder import validate_sql_step_query
from backend.pipeline.exceptions import (
    InvalidYAMLError,
    MissingRequiredFieldError,
    PipelineYAMLTooLargeError,
)
from backend.utils.string_utils import (
    find_closest_column,
    is_safe_filename,
    is_valid_identifier,
)

logger = logging.getLogger(__name__)

# M-164: hard cap on raw pipeline YAML accepted for parsing. Pipelines are
# kilobytes; a 51-step pipeline serializes to ~10KB. Anything beyond this
# is a client bug or a DoS attempt — reject before PyYAML allocates.
# Routes should map PipelineYAMLTooLargeError to HTTP 413.
MAX_PIPELINE_YAML_BYTES = 256_000

# M-112/M-113: the only fill_nulls strategies the batch executor
# implements (backend/pipeline/steps/_core.py::execute_fill_nulls).
# Anything else silently no-ops there, so the validator rejects it here.
VALID_FILL_NULLS_STRATEGIES = frozenset({
    "constant", "forward_fill", "backward_fill", "mean", "median", "mode",
})


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
    SQL = "sql"
    WASM_COMPUTE = "wasm_compute"
    TRANSFORM = "transform"
    STREAM_CONSUME = "stream_consume"
    STREAM_PUBLISH = "stream_publish"


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
    contract: dict | None = None  # data contract constraints, validated post-execution


@dataclass
class LoadStepConfig(StepConfig):
    """Configuration for a file load step."""

    file_id: str = ""
    alias: str | None = None


@dataclass
class FilterStepConfig(StepConfig):
    """Configuration for a row filter step."""

    input: str = ""
    column: str = ""
    operator: FilterOperator = FilterOperator.EQUALS
    value: str | int | float | None = None


@dataclass
class SelectStepConfig(StepConfig):
    """Configuration for a column projection (select) step."""

    input: str = ""
    columns: list[str] = field(default_factory=list)


@dataclass
class RenameStepConfig(StepConfig):
    """Configuration for a column rename step."""

    input: str = ""
    mapping: dict[str, str] = field(default_factory=dict)


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
    group_by: list[str] = field(default_factory=list)
    aggregations: list[dict[str, str]] = field(default_factory=list)


@dataclass
class SortStepConfig(StepConfig):
    """Configuration for a row sort step."""

    input: str = ""
    by: str = ""
    order: SortOrder = SortOrder.ASC
    ascending: object = None


@dataclass
class SaveStepConfig(StepConfig):
    """Configuration for saving the output to a file."""

    input: str = ""
    filename: str = ""


@dataclass
class ValidateStepConfig(StepConfig):
    """Configuration for data quality validation step."""

    input: str = ""
    rules: list[dict] = field(default_factory=list)


@dataclass
class PivotStepConfig(StepConfig):
    """Configuration for a pivot step (long to wide)."""

    input: str = ""
    index: list[str] = field(default_factory=list)
    columns: str = ""
    values: str = ""
    aggfunc: str = "sum"
    fill_value: Any = 0


@dataclass
class UnpivotStepConfig(StepConfig):
    """Configuration for an unpivot step (wide to long)."""

    input: str = ""
    id_vars: list[str] = field(default_factory=list)
    value_vars: list[str] = field(default_factory=list)
    var_name: str = "variable"
    value_name: str = "value"


@dataclass
class DeduplicateStepConfig(StepConfig):
    """Configuration for a deduplicate step."""

    input: str = ""
    subset: list[str] | None = None
    keep: str = "first"


@dataclass
class FillNullsStepConfig(StepConfig):
    """Configuration for a fill nulls step."""

    input: str = ""
    strategy: str = "constant"
    columns: list[str] = field(default_factory=list)
    constant_value: Any = None


@dataclass
class SampleStepConfig(StepConfig):
    """Configuration for a sample step."""

    input: str = ""
    n: int | None = None
    fraction: float | None = None
    random_state: int = 42
    stratify_by: str | None = None


@dataclass
class SqlStepConfig(StepConfig):
    """Configuration for SQL transformation step.

    The query must reference {{input}} to access the upstream DataFrame.
    Only SELECT and WITH (CTE) queries are permitted for safety.
    """

    input: str = ""
    query: str = ""


@dataclass
class WasmComputeStepConfig(StepConfig):
    """Configuration for a WebAssembly UDF compute step."""

    input: str = ""
    wasm_file_id: str = ""
    function: str = ""
    input_columns: list[str] = field(default_factory=list)
    output_column: str = ""


@dataclass
class TransformStepConfig(StepConfig):
    """Configuration for a row-level transform step."""

    input: str = ""
    expression: str = ""


@dataclass
class StreamConsumeStepConfig(StepConfig):
    """Configuration for a streaming consume step (Redpanda/Kafka source)."""

    topic: str = ""
    consumer_group: str = ""
    batch_size: int = 1000
    batch_timeout_ms: int = 5000
    deserialize: str = "json"


@dataclass
class StreamPublishStepConfig(StepConfig):
    """Configuration for a streaming publish step (Redpanda/Kafka sink)."""

    input: str = ""
    topic: str = ""
    serialize: str = "json"
    key_column: str | None = None


@dataclass
class PipelineConfig:
    """Fully typed pipeline configuration parsed from YAML."""

    name: str
    description: str | None
    steps: list[StepConfig]


@dataclass
class ValidationError:
    """A single validation error found during pipeline config validation."""

    step_name: str | None
    field: str
    message: str
    suggestion: str | None = None


@dataclass
class ValidationWarning:
    """A non-blocking warning about the pipeline configuration."""

    step_name: str | None
    message: str


@dataclass
class ValidationResult:
    """Aggregated result of pipeline configuration validation."""

    is_valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[ValidationWarning] = field(default_factory=list)


def extract_envelope(raw: Any) -> tuple[str, Any]:
    """Split raw parsed YAML into (pipeline_name, raw_steps), leniently.

    M-110: the single shared envelope extraction used by both the
    validator (`_build_pipeline_config` semantics) and the dry-run
    planner. Never raises: ragged input (scalar root, missing keys,
    non-list steps) yields "" / [] shapes and lets the caller decide
    how to report them, instead of AttributeError 500s.
    """
    if not isinstance(raw, dict):
        return "", []
    pipeline_raw = raw.get("pipeline", raw)
    if not isinstance(pipeline_raw, dict):
        return "", []
    name = pipeline_raw.get("name", "")
    if not isinstance(name, str):
        name = ""
    steps = pipeline_raw.get("steps", [])
    return name, steps


_STEP_CONFIG_MAP: dict[StepType, type] = {
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
    StepType.SQL: SqlStepConfig,
    StepType.WASM_COMPUTE: WasmComputeStepConfig,
    StepType.TRANSFORM: TransformStepConfig,
    StepType.STREAM_CONSUME: StreamConsumeStepConfig,
    StepType.STREAM_PUBLISH: StreamPublishStepConfig,
}


_RE_DOUBLED_QUOTED_VALUE = re.compile(
    r'([\w_]+):\s*""([^"]+)""\s*$', re.MULTILINE
)


def _repair_quoted_values(yaml_text: str) -> str:
    """Fix YAML where js-yaml output has doubled quotes on scalar values.

    js-yaml may produce ``file_id: ""uuid""`` instead of ``file_id: "uuid"``
    when round-tripping through incompatible parsers. This strips one layer
    of quoting to recover a parseable YAML string.
    """
    return _RE_DOUBLED_QUOTED_VALUE.sub(r'\1: "\2"', yaml_text)


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

    def parse_raw(self, yaml_string: str) -> dict:
        """Parse a YAML string into a raw Python dictionary."""
        return self._parse_yaml(yaml_string)

    def build_from_dict(self, raw: dict) -> PipelineConfig:
        """Build a PipelineConfig from a raw dictionary."""
        return self._build_pipeline_config(raw)

    def validate(
        self,
        config: PipelineConfig,
        registered_file_ids: set[str],
    ) -> ValidationResult:
        """Run all validation checks on a parsed pipeline configuration.

        Returns a ValidationResult with errors and warnings rather than raising,
        so all issues can be reported at once.
        """
        errors: list[ValidationError] = []
        warnings: list[ValidationWarning] = []

        self._check_pipeline_name(config, errors)
        self._check_has_steps(config, errors)
        self._check_step_count_limit(config, errors)
        if len(config.steps) > settings.MAX_PIPELINE_STEPS:
            # M-015: the step-count cap fires before the O(n^2) reference
            # walk below. Report the cheap errors and stop — validating
            # hundreds of steps that will be rejected anyway wastes CPU
            # and can only add noise to the response.
            return ValidationResult(
                is_valid=False,
                errors=errors,
                warnings=warnings,
            )
        self._check_duplicate_step_names(config, errors)
        self._check_step_name_format(config, errors)
        self._check_step_types(config, errors)
        self._check_required_step_fields(config, errors)
        self._check_step_references(config, errors)
        self._check_load_file_ids(config, registered_file_ids, errors)
        self._check_filter_operators(config, errors)
        self._check_join_configs(config, errors, warnings)
        self._check_aggregate_configs(config, errors)
        self._check_has_save_step(config, errors, warnings)
        self._check_save_filenames(config, errors)
        self._check_validate_rules(config, errors)
        self._check_sql_steps(config, errors)
        self._check_contracts(config, errors)
        self._check_unsupported_runtime_steps(config, errors)

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _parse_yaml(self, yaml_string: str) -> dict:
        """Parse raw YAML string into a dictionary."""
        if not isinstance(yaml_string, str):
            raise InvalidYAMLError("Pipeline YAML must be a string")
        # M-164: reject oversized payloads before PyYAML allocates.
        size = len(yaml_string.encode("utf-8"))
        if size > MAX_PIPELINE_YAML_BYTES:
            raise PipelineYAMLTooLargeError(size, MAX_PIPELINE_YAML_BYTES)
        try:
            # Strip potential diff markers before parsing
            from backend.pipeline.diff_utils import strip_diff_markers
            cleaned_yaml = strip_diff_markers(yaml_string)
            raw = yaml.safe_load(cleaned_yaml.strip())
        except RecursionError as exc:
            # M-100: deeply nested YAML (e.g. "[[[[...") exhausts the
            # parser stack. Report a 400 with step context, never a 500.
            raise InvalidYAMLError(
                "Pipeline YAML is nested too deeply; "
                "reduce nesting levels and retry"
            ) from exc
        except yaml.YAMLError as exc:
            # Attempt auto-recovery for js-yaml vs PyYAML incompatibilities
            try:
                from backend.pipeline.diff_utils import strip_diff_markers
                cleaned_yaml = strip_diff_markers(yaml_string).strip()
                repaired = _repair_quoted_values(cleaned_yaml)
                raw = yaml.safe_load(repaired)
            except RecursionError as nested_exc:
                raise InvalidYAMLError(
                    "Pipeline YAML is nested too deeply; "
                    "reduce nesting levels and retry"
                ) from nested_exc
            except yaml.YAMLError:
                line = getattr(exc, "problem_mark", None)
                line_num = line.line + 1 if line else None
                raise InvalidYAMLError(str(exc), line=line_num) from exc

        if not isinstance(raw, dict):
            raise InvalidYAMLError(
                "YAML root must be a mapping, not a scalar or list")
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
            # Return a base StepConfig so validation can report the invalid
            # type
            return StepConfig(
                name=step_name,
                step_type=step_type_str)

        config = self._build_typed_step(step_name, step_type, step_raw)
        raw_contract = step_raw.get("contract")
        if raw_contract is not None:
            config.contract = raw_contract
        return config

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
                raw_aggs = [{"column": col, "function": func}
                            for col, func in raw_aggs.items()]
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
            index_list = [index_raw] if isinstance(
                index_raw, str) else index_raw
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
                strategy=raw.get("strategy", "constant"),
                constant_value=raw.get("constant_value"),
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
        if step_type == StepType.SQL:
            return SqlStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                query=raw.get("query", ""),
            )
        if step_type == StepType.WASM_COMPUTE:
            return WasmComputeStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                wasm_file_id=raw.get("wasm_file_id", ""),
                function=raw.get("function", ""),
                input_columns=raw.get("input_columns", []),
                output_column=raw.get("output_column", ""),
            )
        if step_type == StepType.TRANSFORM:
            return TransformStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                expression=raw.get("expression", ""),
            )
        if step_type == StepType.STREAM_CONSUME:
            # M-113: keep raw ints; wrong types/ranges are reported by
            # _check_required_step_fields with step context (raising here
            # would break the validate-returns-errors contract).
            return StreamConsumeStepConfig(
                name=name,
                step_type=step_type,
                topic=raw.get("topic", ""),
                consumer_group=raw.get("consumer_group", ""),
                batch_size=raw.get("batch_size", 1000),
                batch_timeout_ms=raw.get("batch_timeout_ms", 5000),
                deserialize=raw.get("deserialize", "json"),
            )
        if step_type == StepType.STREAM_PUBLISH:
            return StreamPublishStepConfig(
                name=name,
                step_type=step_type,
                input=raw.get("input", ""),
                topic=raw.get("topic", ""),
                serialize=raw.get("serialize", "json"),
                key_column=raw.get("key_column"),
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
            operator = operator_str

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
            how = how_str

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
            order = SortOrder(str(order_str).lower())
        except ValueError:
            order = order_str

        return SortStepConfig(
            name=name,
            step_type=step_type,
            input=raw.get("input", ""),
            by=raw.get("by", ""),
            order=order,
            ascending=raw.get("ascending"),
        )

    def _check_pipeline_name(
        self, config: PipelineConfig, errors: list[ValidationError]
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
        self, config: PipelineConfig, errors: list[ValidationError]
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
        self, config: PipelineConfig, errors: list[ValidationError]
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
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check 3: No duplicate step names."""
        seen: dict[str, int] = {}
        for step in config.steps:
            # M-113: unhashable/non-string names are reported by
            # _check_required_step_fields; skip them here.
            if not isinstance(step.name, str):
                continue
            seen[step.name] = seen.get(step.name, 0) + 1
        for name, count in seen.items():
            if count > 1:
                errors.append(
                    ValidationError(
                        step_name=name,
                        field="name",
                        message=f"Step name '{name}' is used {count} times. Names must be unique.",
                    ))

    def _check_step_name_format(
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check 12: Step names only contain alphanumeric characters and underscores."""
        for step in config.steps:
            # M-113: non-string names are reported by
            # _check_required_step_fields; skip them here.
            if not isinstance(step.name, str) or not step.name:
                continue
            if not is_valid_identifier(step.name):
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
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check 4: Every step has a valid type."""
        valid_types = [t.value for t in StepType]
        for step in config.steps:
            if not isinstance(step.step_type, StepType):
                suggestion = find_closest_column(
                    str(step.step_type), valid_types)
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="type",
                        message=f"Invalid step type '{step.step_type}'. Valid types: {valid_types}",
                        suggestion=suggestion,
                    ))

    @staticmethod
    def _must_be_nonempty_str(
        errors: list[ValidationError],
        step_name: str,
        value: Any,
        field: str,
        what: str,
    ) -> bool:
        """M-113: report wrong-typed/empty scalar params as 400s, not 500s."""
        if not isinstance(value, str) or not value.strip():
            errors.append(
                ValidationError(
                    step_name=step_name,
                    field=field,
                    message=f"{what} must be a non-empty string",
                )
            )
            return False
        return True

    @staticmethod
    def _must_be_str_list(
        errors: list[ValidationError],
        step_name: str,
        value: Any,
        field: str,
        what: str,
    ) -> bool:
        """M-113: YAML `on:`/`yes:` keys parse as booleans; reject them."""
        if not isinstance(value, list) or not value:
            errors.append(
                ValidationError(
                    step_name=step_name,
                    field=field,
                    message=f"{what} must be a non-empty list of strings",
                )
            )
            return False
        bad = [v for v in value if not isinstance(v, str) or not v.strip()]
        if bad:
            errors.append(
                ValidationError(
                    step_name=step_name,
                    field=field,
                    message=(
                        f"{what} must contain only non-empty strings; "
                        f"got {bad!r} (note: bare YAML `on`/`yes`/`no` "
                        f"parse as booleans — quote them)"
                    ),
                )
            )
            return False
        return True

    def _check_required_step_fields(
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Validate required step fields before semantic reference checks."""
        for step in config.steps:
            step_name = step.name if isinstance(
                getattr(step, "name", None), str) and step.name else "<unnamed>"

            if not isinstance(getattr(step, "name", None), str) or not step.name.strip():
                errors.append(
                    ValidationError(
                        step_name=step_name,
                        field="name",
                        message="Step name must be a non-empty string",
                    )
                )
                continue

            if not isinstance(step.step_type, StepType):
                continue

            if isinstance(step, LoadStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.file_id, "file_id",
                    "Load step must specify a non-empty file_id")
                continue

            if isinstance(step, JoinStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.left, "left",
                    "Join step must specify a non-empty left input")
                self._must_be_nonempty_str(
                    errors, step_name, step.right, "right",
                    "Join step must specify a non-empty right input")
            elif not isinstance(step, StreamConsumeStepConfig):
                input_ref = getattr(step, "input", None)
                if isinstance(input_ref, str) and not input_ref.strip():
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="input",
                            message="Step must specify a non-empty input reference",
                        ))
                # Non-string input refs are reported by
                # _validate_step_reference with step context.

            if isinstance(step, FilterStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.column, "column",
                    "Filter step must specify a non-empty column")
                if (
                    isinstance(step.operator, FilterOperator)
                    and step.operator
                    not in {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}
                    and step.value is None
                ):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="value",
                            message="Filter step must specify a value for this operator",
                        ))

            if isinstance(step, SelectStepConfig):
                self._must_be_str_list(
                    errors, step_name, step.columns, "columns",
                    "Select step 'columns'")

            if isinstance(step, RenameStepConfig):
                if not isinstance(step.mapping, dict) or not step.mapping:
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="mapping",
                            message="Rename step 'mapping' must be a non-empty mapping",
                        ))
                elif any(not isinstance(k, str) or not isinstance(v, str)
                         for k, v in step.mapping.items()):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="mapping",
                            message="Rename step 'mapping' keys and values must be strings",
                        ))

            if isinstance(step, AggregateStepConfig):
                self._must_be_str_list(
                    errors, step_name, step.group_by, "group_by",
                    "Aggregate step 'group_by'")

            if isinstance(step, SortStepConfig):
                by_vals = step.by if isinstance(step.by, list) else [step.by]
                if not any(isinstance(b, str) and b.strip() for b in by_vals):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="by",
                            message="Sort step must specify a non-empty string 'by' column",
                        ))
                if not isinstance(step.order, SortOrder):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="order",
                            message="Sort step 'order' must be 'asc' or 'desc'",
                        ))

            if (isinstance(step, ValidateStepConfig)
                    and (not isinstance(step.rules, list)
                         or not step.rules)):
                errors.append(
                    ValidationError(
                        step_name=step_name,
                        field="rules",
                        message="Validate step 'rules' must be a non-empty list",
                    ))

            if isinstance(step, PivotStepConfig):
                index_vals = (step.index if isinstance(step.index, list)
                              else [step.index])
                if not any(isinstance(v, str) and v.strip()
                           for v in index_vals):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="index",
                            message="Pivot step must specify at least one string index column",
                        ))
                self._must_be_nonempty_str(
                    errors, step_name, step.columns, "columns",
                    "Pivot step must specify a non-empty columns field")
                self._must_be_nonempty_str(
                    errors, step_name, step.values, "values",
                    "Pivot step must specify a non-empty values field")

            if isinstance(step, UnpivotStepConfig):
                self._must_be_str_list(
                    errors, step_name, step.id_vars, "id_vars",
                    "Unpivot step 'id_vars'")
                self._must_be_str_list(
                    errors, step_name, step.value_vars, "value_vars",
                    "Unpivot step 'value_vars'")

            if isinstance(step, FillNullsStepConfig):
                if step.strategy not in VALID_FILL_NULLS_STRATEGIES:
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="strategy",
                            message=(
                                f"Fill nulls step strategy must be one of "
                                f"{sorted(VALID_FILL_NULLS_STRATEGIES)}; "
                                f"got {step.strategy!r}"
                            ),
                        ))
                if (step.strategy == "constant"
                        and step.constant_value is None):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="value",
                            message="Fill nulls step with 'constant' strategy must provide a value",
                        ))
                if not isinstance(step.columns, list) or not all(
                        isinstance(c, str) for c in step.columns):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="columns",
                            message="Fill nulls step 'columns' must be a list of strings",
                        ))

            if isinstance(step, SampleStepConfig):
                if step.n is None and step.fraction is None:
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="sample",
                            message="Sample step must specify either 'n' or 'fraction'",
                        ))
                if step.n is not None and step.fraction is not None:
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="sample",
                            message="Sample step must specify only one of 'n' or 'fraction'",
                        ))
                if (step.n is not None
                        and (not isinstance(step.n, int)
                             or isinstance(step.n, bool) or step.n <= 0)):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="sample",
                            message="Sample step 'n' must be a positive integer",
                        ))
                if step.fraction is not None and (
                        not isinstance(step.fraction, (int, float))
                        or isinstance(step.fraction, bool)
                        or not 0 < step.fraction <= 1):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="sample",
                            message="Sample step 'fraction' must be a number in (0, 1]",
                        ))
                if (step.random_state is not None
                        and not isinstance(step.random_state, int)):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="random_state",
                            message="Sample step 'random_state' must be an integer",
                        ))
                if (step.stratify_by is not None
                        and (not isinstance(step.stratify_by, str)
                             or not step.stratify_by.strip())):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="stratify_by",
                            message="Sample step 'stratify_by' must be a non-empty string",
                        ))

            if isinstance(step, SqlStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.query, "query",
                    "SQL step must provide a non-empty query")

            if isinstance(step, WasmComputeStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.wasm_file_id, "wasm_file_id",
                    "wasm_compute step must specify a non-empty wasm_file_id")
                self._must_be_nonempty_str(
                    errors, step_name, step.function, "function",
                    "wasm_compute step must specify a non-empty function name")
                if (not isinstance(step.input_columns, list)
                        or not step.input_columns or not all(
                            isinstance(c, str) and c.strip()
                            for c in step.input_columns)):
                    errors.append(
                        ValidationError(
                            step_name=step_name,
                            field="input_columns",
                            message="wasm_compute step must specify at least one input_column",
                        ))
                self._must_be_nonempty_str(
                    errors, step_name, step.output_column, "output_column",
                    "wasm_compute step must specify a non-empty output_column")

            if isinstance(step, TransformStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.expression, "expression",
                    "Transform step must specify a non-empty expression")

            if isinstance(step, StreamConsumeStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.topic, "topic",
                    "Stream consume step must specify a non-empty topic")
                for int_field in ("batch_size", "batch_timeout_ms"):
                    int_value = getattr(step, int_field, None)
                    if (not isinstance(int_value, int)
                            or isinstance(int_value, bool)
                            or int_value <= 0):
                        errors.append(
                            ValidationError(
                                step_name=step_name,
                                field=int_field,
                                message=(
                                    f"Stream consume step '{int_field}' "
                                    f"must be a positive integer"
                                ),
                            ))

            if isinstance(step, StreamPublishStepConfig):
                self._must_be_nonempty_str(
                    errors, step_name, step.topic, "topic",
                    "Stream publish step must specify a non-empty topic")

    def _check_step_references(
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check 5: All input/left/right references point to earlier steps."""
        seen_steps: set[str] = set()
        seen_ordered: list[str] = []
        for step in config.steps:
            self._validate_step_reference(step, seen_steps, errors)
            if isinstance(step.name, str):
                seen_steps.add(step.name)
                seen_ordered.append(step.name)

    def _validate_step_reference(
        self,
        step: StepConfig,
        available_steps: set[str],
        errors: list[ValidationError],
    ) -> None:
        """Validate all reference fields on a single step config."""
        refs_to_check: list[tuple] = []

        if isinstance(step, JoinStepConfig):
            # M-113: non-string join operands are a 400, not a crash.
            # (Empty strings are already reported by
            # _check_required_step_fields, so only flag wrong types here.)
            for field_name in ("left", "right"):
                ref_value = getattr(step, field_name, "")
                if not isinstance(ref_value, str):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field=field_name,
                            message=(
                                f"Join step '{field_name}' must be a string, "
                                f"got {type(ref_value).__name__}"
                            ),
                        )
                    )
                elif ref_value.strip():
                    refs_to_check.append((field_name, ref_value))
        else:
            input_ref = getattr(step, "input", None)
            if input_ref is None:
                return
            if not isinstance(input_ref, str):
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="input",
                        message=(
                            "Step input reference must be a string, got "
                            f"{type(input_ref).__name__}"
                        ),
                    )
                )
            elif input_ref.strip():
                refs_to_check.append(("input", input_ref))

        for field_name, ref_value in refs_to_check:
            # M-015: set membership is O(1); the old list scan made this
            # whole check O(n^2) in the step count.
            if ref_value not in available_steps:
                suggestion = find_closest_column(
                    ref_value, sorted(available_steps))
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field=field_name,
                        message=(
                            f"References '{ref_value}' which does not exist "
                            f"or comes after this step. Available: "
                            f"{sorted(available_steps)}"
                        ),
                        suggestion=suggestion,
                    )
                )

    def _check_load_file_ids(
        self,
        config: PipelineConfig,
        registered_file_ids: set[str],
        errors: list[ValidationError],
    ) -> None:
        """Check 6: Every load step's file_id exists in registered_file_ids."""
        for step in config.steps:
            # M-113: wrong-typed file_ids are reported by
            # _check_required_step_fields; skip them here.
            if not (
                isinstance(step, LoadStepConfig)
                and isinstance(step.file_id, str)
                and step.file_id.strip()
            ):
                continue
            if step.file_id not in registered_file_ids:
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="file_id",
                        message=(
                            f"file_id '{step.file_id}' is not registered. Registered IDs: {sorted(registered_file_ids)}"),
                    ))

    def _check_filter_operators(
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check 7: Every filter step has a valid operator."""
        valid_ops = [op.value for op in FilterOperator]
        for step in config.steps:
            if isinstance(step, FilterStepConfig) and not isinstance(
                step.operator, FilterOperator
            ):
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
        errors: list[ValidationError],
        warnings: list[ValidationWarning],
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
                        ))
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
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Checks 9 & 10: Aggregation step has aggregations with valid functions."""
        for step in config.steps:
            if isinstance(step, AggregateStepConfig):
                if not isinstance(step.aggregations, list):
                    errors.append(
                        ValidationError(
                            step_name=step.name, field="aggregations", message=(
                                "Aggregate step 'aggregations' must be a list "
                                "of {column, function} mappings"),
                        ))
                    continue
                if not step.aggregations:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="aggregations",
                            message="Aggregate step must define at least one aggregation",
                        ))
                for agg in step.aggregations:
                    if not isinstance(agg, dict):
                        errors.append(
                            ValidationError(
                                step_name=step.name,
                                field="aggregations",
                                message=(
                                    "Each aggregation must be a "
                                    "{column, function} mapping; "
                                    f"got {agg!r}"
                                ),
                            ))
                        continue
                    col_name = agg.get("column", "")
                    if not isinstance(col_name, str) or not col_name.strip():
                        errors.append(
                            ValidationError(
                                step_name=step.name,
                                field="aggregations.column",
                                message="Each aggregation must specify a non-empty string 'column'",
                            ))
                    func_name = agg.get("function", "")
                    if func_name not in VALID_AGGREGATION_FUNCTIONS:
                        errors.append(
                            ValidationError(
                                step_name=step.name, field="aggregations.function", message=(
                                    f"Invalid aggregation function '{func_name}'. Valid functions: {sorted(VALID_AGGREGATION_FUNCTIONS)}"), ))

    def _check_has_save_step(
        self,
        config: PipelineConfig,
        errors: list[ValidationError],
        warnings: list[ValidationWarning],
    ) -> None:
        """Check 11: At least one save step or stream_publish step exists."""
        has_save = any(isinstance(step, SaveStepConfig)
                       for step in config.steps)
        has_stream_publish = any(
            isinstance(step, StreamPublishStepConfig)
            for step in config.steps)
        if not has_save and not has_stream_publish:
            warnings.append(
                ValidationWarning(
                    step_name=None, message=(
                        "Pipeline has no 'save' or 'stream_publish' step. Results will be computed "
                        "but not persisted to an output file."), ))

    def _check_save_filenames(
        self, config: PipelineConfig, errors: list[ValidationError]
    ) -> None:
        """Check that all save step filenames are safe and don't allow path traversal."""
        seen_filenames: dict[str, str] = {}
        for step in config.steps:
            if isinstance(step, SaveStepConfig):
                if not isinstance(step.filename, str) or not step.filename:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="filename",
                            message="Save step must specify a string filename",
                        )
                    )
                elif not is_safe_filename(step.filename):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field="filename",
                            message=f"Invalid filename '{step.filename}'. Path traversal characters are forbidden.",
                        ))
                else:
                    # M-143: run-scoped save outputs live at
                    # outputs/{run_id}/{filename}, so two saves with the
                    # same filename in one pipeline overwrite each other.
                    # Reject duplicates instead of silently clobbering.
                    first = seen_filenames.setdefault(
                        step.filename, step.name)
                    if first != step.name:
                        errors.append(
                            ValidationError(
                                step_name=step.name,
                                field="filename",
                                message=(
                                    f"Duplicate save filename '{step.filename}' "
                                    f"(also used by step '{first}'). Each save "
                                    f"step must use a unique filename."
                                ),
                            ))

    # M-113: params each check type needs beyond "check". Missing params
    # used to surface as KeyError 500s inside the validator at runtime.
    _VALIDATE_RULE_PARAMS: ClassVar[dict[str, tuple[str, ...]]] = {
        "not_null": ("column",),
        "not_empty": ("column",),
        "greater_than": ("column", "value"),
        "less_than": ("column", "value"),
        "between": ("column", "min", "max"),
        "in_values": ("column", "values"),
        "matches_pattern": ("column", "pattern"),
        "no_duplicates": ("column",),
        "min_rows": ("value",),
        "max_rows": ("value",),
        "date_format": ("column", "format"),
        "positive": ("column",),
    }

    def _check_validate_rules(
        self,
        config: PipelineConfig,
        errors: list[ValidationError],
    ) -> None:
        """Check 12: Validate step rules use supported check types."""
        from backend.pipeline.validators import SUPPORTED_CHECKS

        for step in config.steps:
            if not isinstance(step, ValidateStepConfig):
                continue
            rules = step.rules if isinstance(step.rules, list) else []
            for idx, rule in enumerate(rules):
                field = f"rules[{idx}]"
                if not isinstance(rule, dict):
                    errors.append(
                        ValidationError(
                            step_name=step.name, field=field, message=(
                                f"Validate rule #{idx} must be a mapping; "
                                f"got {type(rule).__name__}"),
                        ))
                    continue
                check = rule.get("check", "")
                if check not in SUPPORTED_CHECKS:
                    errors.append(
                        ValidationError(
                            step_name=step.name, field="rules", message=(
                                f"Unknown check type '{check}'. Supported: {', '.join(sorted(SUPPORTED_CHECKS))}"), ))
                    continue
                for param in self._VALIDATE_RULE_PARAMS.get(check, ()):
                    if param not in rule or rule[param] is None:
                        errors.append(
                            ValidationError(
                                step_name=step.name, field=field, message=(
                                    f"Validate rule #{idx} (check "
                                    f"'{check}') is missing required "
                                    f"parameter '{param}'"),
                            ))

    def _check_sql_steps(
        self,
        config: PipelineConfig,
        errors: list[ValidationError],
    ) -> None:
        """Validate SQL step safety and placeholder usage."""
        for step in config.steps:
            if not isinstance(step, SqlStepConfig):
                continue
            # M-113: wrong-typed queries are reported by
            # _check_required_step_fields; skip them here.
            if not isinstance(step.query, str):
                continue
            try:
                validate_sql_step_query(
                    step.query, require_input_placeholder=True)
            except ValueError as exc:
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="query",
                        message=str(exc),
                    )
                )

    def _check_contracts(
        self,
        config: PipelineConfig,
        errors: list[ValidationError],
    ) -> None:
        """M-113: Validate inline step contracts structurally.

        Contracts used to flow straight to the executor, where a
        malformed mapping (missing name, bad dtype, non-list columns)
        raised a raw KeyError/ValueError at runtime. Reject them here
        with step context instead.
        """
        from backend.pipeline.contracts import DtypeKind

        valid_dtypes = {d.value for d in DtypeKind}

        for step in config.steps:
            contract = getattr(step, "contract", None)
            if contract is None:
                continue
            if not isinstance(contract, dict):
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="contract",
                        message="Step contract must be a mapping",
                    )
                )
                continue
            columns = contract.get("columns", [])
            if not isinstance(columns, list):
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="contract",
                        message="Step contract 'columns' must be a list",
                    )
                )
                continue
            for idx, col in enumerate(columns):
                if not isinstance(col, dict) or not isinstance(
                        col.get("name"), str):
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field=f"contract.columns[{idx}]",
                            message="Each contract column must be a mapping with a string 'name'",
                        ))
                    continue
                dtype_raw = col.get("dtype")
                if dtype_raw is not None and dtype_raw not in valid_dtypes:
                    errors.append(
                        ValidationError(
                            step_name=step.name,
                            field=f"contract.columns[{idx}].dtype",
                            message=(
                                f"Unknown contract dtype '{dtype_raw}'. "
                                f"Valid: {sorted(valid_dtypes)}"),
                        ))

    def _check_unsupported_runtime_steps(
        self,
        config: PipelineConfig,
        errors: list[ValidationError],
    ) -> None:
        """Reject step types the batch executors cannot run.

        `transform` parses but has no executor in StepExecutor or
        SmartExecutor, so it would fail at runtime with a bare ValueError.
        Reject it here so the author gets an actionable 400 instead.
        (`stream_consume` is intentionally allowed: the run endpoint routes
        those pipelines to the streaming daemon.)
        """
        for step in config.steps:
            step_type = getattr(step, "step_type", None)
            if step_type == StepType.TRANSFORM:
                errors.append(
                    ValidationError(
                        step_name=step.name,
                        field="type",
                        message=(
                            "Step type 'transform' is not executable: "
                            "no batch executor is registered for it. "
                            "Use filter, sql, or another executable step type instead."
                        ),
                    )
                )
