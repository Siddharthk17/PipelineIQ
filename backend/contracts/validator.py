"""Data contract validation engine.

Parses YAML contract definitions and validates pipeline output
against promised column types, null thresholds, and row count bounds.

Extracted from contracts.__init__ for cleaner separation of concerns.
Contract validation logic belongs in a single module, not mixed with
API or pipeline-layer concerns.

Fail-closed by design (M-073): every check that cannot be evaluated —
unknown declared dtype, malformed column spec, invalid regex, or a
compute error on an incompatible column (M-059) — produces an
``error`` violation instead of silently passing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.compute as pc
import yaml

logger = logging.getLogger(__name__)

TYPE_CATEGORY_MAP: dict[str, str] = {
    "int8": "integer",
    "int16": "integer",
    "int32": "integer",
    "int64": "integer",
    "uint8": "integer",
    "uint16": "integer",
    "uint32": "integer",
    "uint64": "integer",
    "float16": "float",
    "float32": "float",
    "float64": "float",
    "double": "float",
    "bool": "boolean",
    "object": "string",
    "string": "string",
    "large_string": "string",
    "utf8": "string",
    "large_utf8": "string",
    "timestamp[ns]": "datetime",
    "timestamp[us]": "datetime",
    "timestamp[ms]": "datetime",
    "timestamp[s]": "datetime",
    "date32": "datetime",
    "date64": "datetime",
}

# Declared ``type`` values the validator understands. Anything else is an
# unknown dtype and fails closed (M-073) instead of being string-compared
# against the Arrow type name. ``any`` disables the type check, mirroring
# DtypeKind.ANY in backend/pipeline/contracts.py.
KNOWN_TYPE_CATEGORIES: frozenset[str] = frozenset(
    {"integer", "float", "string", "boolean", "datetime", "any"}
)
KNOWN_TYPE_NAMES: frozenset[str] = (
    KNOWN_TYPE_CATEGORIES | frozenset(TYPE_CATEGORY_MAP)
)

# Cap on sampled offending values embedded in violation messages.
_MAX_SAMPLE_VALUES = 5


@dataclass
class ContractViolation:
    column: str
    rule: str
    severity: str
    message: str
    actual: str | None = None
    expected: str | None = None


@dataclass
class ContractValidationResult:
    passed: bool
    violations: list[ContractViolation] = field(default_factory=list)


@dataclass
class BreachReport:
    """Aggregated contract validation result with enforcement decision.

    Combines the raw validation result with the contract's severity setting
    to determine whether the run should be blocked or just alerted.
    """

    has_breaches: bool
    should_block_run: bool
    breaches: list[ContractViolation] = field(default_factory=list)
    summary: str = ""


def build_breach_report(
    validation: ContractValidationResult,
    severity: str = "warn",
) -> BreachReport:
    """Wrap a ContractValidationResult into a BreachReport with enforcement decision.

    Args:
        validation: Raw validation result from validate_against_contract
        severity: Contract severity — 'block' or 'warn'

    Returns:
        BreachReport with enforcement decision
    """
    if validation.passed:
        return BreachReport(
            has_breaches=False,
            should_block_run=False,
            breaches=[],
            summary="All contract checks passed",
        )

    is_block = severity == "block"
    total = len(validation.violations)
    errors = sum(1 for v in validation.violations if v.severity == "error")
    warnings_count = total - errors

    has_blocking_violation = errors > 0
    should_block = is_block and has_blocking_violation

    if should_block:
        summary = (
            f"Contract BREACH (block): {errors} error(s), {warnings_count} warning(s) "
            f"— run marked CONTRACT_VIOLATION, downstream pipelines blocked"
        )
    elif is_block:
        summary = (
            f"Contract breach (block/severity): {errors} error(s), {warnings_count} warning(s) "
            f"— warnings only, run stays COMPLETED"
        )
    else:
        summary = (
            f"Contract breach (warn): {errors} error(s), {warnings_count} warning(s) "
            f"— run stays COMPLETED"
        )

    return BreachReport(
        has_breaches=True,
        should_block_run=should_block,
        breaches=validation.violations,
        summary=summary,
    )


def _arrow_type_category(dtype: pa.DataType) -> str:
    """Map a PyArrow type to its semantic category."""
    type_name = str(dtype).lower()
    if pa.types.is_integer(dtype):
        return "integer"
    if pa.types.is_floating(dtype):
        return "float"
    if pa.types.is_boolean(dtype):
        return "boolean"
    if pa.types.is_string(dtype) or pa.types.is_large_string(dtype):
        return "string"
    if pa.types.is_timestamp(dtype) or pa.types.is_date(dtype):
        return "datetime"
    return TYPE_CATEGORY_MAP.get(type_name, type_name)


def _is_nullable(value: object) -> bool:
    """Fail-closed nullability flag (M-073).

    NULLs are denied unless the contract explicitly allows them. String
    spellings such as ``"false"`` are truthy in YAML-quirk terms but must
    NOT open the gate — only ``true`` (or an explicit true-ish string)
    permits nulls.
    """
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "yes", "y"):
        return True
    return False


def _check_pattern(
    col_name: str, col: pa.ChunkedArray, pattern: object
) -> ContractViolation | None:
    """Evaluate a ``pattern``/``regex`` rule, fail-closed (M-073).

    Returns a violation when the regex is invalid (``invalid_regex``) or
    when non-null values do not match (``pattern_mismatch``); None when
    every non-null value matches. Nulls are governed by the ``nullable``
    rule, not by the pattern.
    """
    try:
        rx = re.compile(str(pattern))
    except re.error as exc:
        return ContractViolation(
            column=col_name,
            rule="invalid_regex",
            severity="error",
            message=f"Column '{col_name}' has an invalid regex pattern: {exc}",
            expected=str(pattern),
            actual="invalid regex",
        )
    bad: list[str] = []
    for v in col.to_pylist():
        if v is None:
            continue
        if rx.match(str(v)) is None:
            bad.append(str(v))
            if len(bad) >= _MAX_SAMPLE_VALUES:
                break
    if bad:
        return ContractViolation(
            column=col_name,
            rule="pattern_mismatch",
            severity="error",
            message=f"Column '{col_name}' has values not matching pattern '{pattern}': {bad}",
            expected=str(pattern),
            actual=str(bad),
        )
    return None


def _parse_contract(yaml_content: str) -> dict:
    """Parse contract YAML into a validated dict."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError:
        logger.exception("Failed to parse contract YAML")
        return {}

    if not isinstance(data, dict):
        return {}
    return data


def validate_against_contract(
    output_table: pa.Table | None,
    yaml_content: str,
) -> ContractValidationResult:
    """Validate pipeline output against a YAML data contract.

    Args:
        output_table: Arrow Table produced by the pipeline's final step
        yaml_content: Raw YAML string from the active PipelineContract

    Returns:
        ContractValidationResult with all detected violations
    """
    contract = _parse_contract(yaml_content)
    if not contract:
        return ContractValidationResult(
            passed=False,
            violations=[
                ContractViolation(
                    column="__contract__",
                    rule="parse_error",
                    severity="error",
                    message="Contract YAML is empty or invalid",
                )
            ],
        )

    if output_table is None:
        return ContractValidationResult(
            passed=False,
            violations=[
                ContractViolation(
                    column="__all__",
                    rule="no_output",
                    severity="error",
                    message="Pipeline produced no output table",
                )
            ],
        )

    violations: list[ContractViolation] = []
    promised_columns: dict = contract.get("columns", {}) or {}
    null_thresholds: dict = contract.get("null_thresholds", {}) or {}
    min_rows = contract.get("min_rows")
    max_rows = contract.get("max_rows")
    actual_columns = set(output_table.schema.names)
    promised_cols = set(promised_columns.keys())

    for col_name, col_spec in promised_columns.items():
        if not isinstance(col_spec, dict):
            # Malformed spec fails closed (M-073): never silently skip.
            violations.append(
                ContractViolation(
                    column=col_name,
                    rule="invalid_spec",
                    severity="error",
                    message=(
                        f"Column '{col_name}' has a malformed contract spec "
                        f"(expected a mapping, got {type(col_spec).__name__})"
                    ),
                    expected="mapping",
                    actual=type(col_spec).__name__,
                )
            )
            continue

        if col_name not in actual_columns:
            violations.append(
                ContractViolation(
                    column=col_name,
                    rule="column_removed",
                    severity="error",
                    message=f"Column '{col_name}' promised by contract but missing from output",
                    expected=col_spec.get("type"),
                    actual="missing",
                )
            )
            continue

        expected_type = col_spec.get("type", "")
        if expected_type:
            expected_name = str(expected_type).lower()
            if expected_name not in KNOWN_TYPE_NAMES:
                # Unknown declared dtype fails closed (M-073).
                violations.append(
                    ContractViolation(
                        column=col_name,
                        rule="unknown_dtype",
                        severity="error",
                        message=(
                            f"Column '{col_name}' declares unknown type "
                            f"'{expected_type}'"
                        ),
                        expected=str(expected_type),
                        actual="unknown dtype",
                    )
                )
            elif expected_name != "any":
                field_idx = output_table.schema.get_field_index(col_name)
                actual_dtype = output_table.schema[field_idx].type
                actual_cat = _arrow_type_category(actual_dtype)
                expected_cat = TYPE_CATEGORY_MAP.get(expected_name, expected_name)

                if actual_cat != expected_cat:
                    violations.append(
                        ContractViolation(
                            column=col_name,
                            rule="type_changed",
                            severity="error",
                            message=f"Column '{col_name}' type mismatch: expected {expected_type}, got {actual_dtype}",
                            expected=str(expected_type),
                            actual=str(actual_dtype),
                        )
                    )

        nullable = col_spec.get("nullable", False)
        if not _is_nullable(nullable):
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            if col.null_count > 0:
                violations.append(
                    ContractViolation(
                        column=col_name,
                        rule="not_null",
                        severity="error",
                        message=f"Column '{col_name}' must not be null but has {col.null_count} null values",
                        expected="0 nulls",
                        actual=f"{col.null_count} nulls",
                    )
                )

        pattern = col_spec.get("pattern", col_spec.get("regex"))
        if pattern is not None:
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            pattern_violation = _check_pattern(col_name, col, pattern)
            if pattern_violation is not None:
                violations.append(pattern_violation)

        min_val = col_spec.get("min_value")
        max_val = col_spec.get("max_value")
        if min_val is not None or max_val is not None:
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            if min_val is not None:
                try:
                    below = pc.less(col, min_val)  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
                    below_count = pc.sum(below).as_py() or 0  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
                except Exception as exc:
                    # Incompatible dtype (M-059) fails closed, never aborts.
                    violations.append(
                        ContractViolation(
                            column=col_name,
                            rule="min_value",
                            severity="error",
                            message=(
                                f"Column '{col_name}' min_value check could not "
                                f"be evaluated on dtype {col.type}: {exc}"
                            ),
                            expected=f">= {min_val}",
                            actual=str(col.type),
                        )
                    )
                else:
                    if below_count > 0:
                        violations.append(
                            ContractViolation(
                                column=col_name,
                                rule="min_value",
                                severity="error",
                                message=f"Column '{col_name}' has {below_count} values below minimum {min_val}",
                                expected=f">= {min_val}",
                                actual=f"{below_count} values below",
                            )
                        )
            if max_val is not None:
                try:
                    above = pc.greater(col, max_val)  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
                    above_count = pc.sum(above).as_py() or 0  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
                except Exception as exc:
                    violations.append(
                        ContractViolation(
                            column=col_name,
                            rule="max_value",
                            severity="error",
                            message=(
                                f"Column '{col_name}' max_value check could not "
                                f"be evaluated on dtype {col.type}: {exc}"
                            ),
                            expected=f"<= {max_val}",
                            actual=str(col.type),
                        )
                    )
                else:
                    if above_count > 0:
                        violations.append(
                            ContractViolation(
                                column=col_name,
                                rule="max_value",
                                severity="error",
                                message=f"Column '{col_name}' has {above_count} values above maximum {max_val}",
                                expected=f"<= {max_val}",
                                actual=f"{above_count} values above",
                            )
                        )

        allowed = col_spec.get("allowed_values")
        if allowed is not None:
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            try:
                unique_vals = pc.unique(col).to_pylist()  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
                allowed_set = set(str(v) for v in allowed)
                disallowed = [v for v in unique_vals if str(v) not in allowed_set]
            except Exception as exc:
                violations.append(
                    ContractViolation(
                        column=col_name,
                        rule="allowed_values",
                        severity="error",
                        message=(
                            f"Column '{col_name}' allowed_values check could not "
                            f"be evaluated: {exc}"
                        ),
                        expected=str(allowed),
                        actual="check error",
                    )
                )
            else:
                if disallowed:
                    violations.append(
                        ContractViolation(
                            column=col_name,
                            rule="allowed_values",
                            severity="error",
                            message=f"Column '{col_name}' has values not in allowed set: {disallowed[:5]}",
                            expected=str(allowed),
                            actual=str(disallowed[:5]),
                        )
                    )

        unique = col_spec.get("unique")
        if unique is True:
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            try:
                unique_count = pc.count_distinct(col).as_py() or 0  # type: ignore[attr-defined]  # pyarrow stubs omit kernels (present at runtime)
            except Exception as exc:
                violations.append(
                    ContractViolation(
                        column=col_name,
                        rule="unique",
                        severity="error",
                        message=(
                            f"Column '{col_name}' uniqueness check could not "
                            f"be evaluated: {exc}"
                        ),
                        expected="all unique",
                        actual="check error",
                    )
                )
            else:
                non_null_count = len(col) - col.null_count
                if unique_count < non_null_count:
                    violations.append(
                        ContractViolation(
                            column=col_name,
                            rule="unique",
                            severity="error",
                            message=f"Column '{col_name}' has duplicates ({unique_count} unique of {non_null_count} non-null)",
                            expected="all unique",
                            actual=f"{unique_count} unique",
                        )
                    )

    for col_name, max_null_pct in null_thresholds.items():
        if col_name not in actual_columns:
            continue

        try:
            threshold = float(max_null_pct)
        except (TypeError, ValueError):
            violations.append(
                ContractViolation(
                    column=col_name,
                    rule="invalid_threshold",
                    severity="error",
                    message=(
                        f"Column '{col_name}' has a non-numeric null threshold: "
                        f"{max_null_pct!r}"
                    ),
                    expected="numeric threshold",
                    actual=str(max_null_pct),
                )
            )
            continue

        field_idx = output_table.schema.get_field_index(col_name)
        col = output_table.column(field_idx)
        null_count = col.null_count
        total_count = len(col)
        if total_count == 0:
            continue

        actual_null_pct = (null_count / total_count) * 100
        if actual_null_pct > threshold:
            violations.append(
                ContractViolation(
                    column=col_name,
                    rule="null_threshold_exceeded",
                    severity="warning",
                    message=f"Column '{col_name}' null rate {actual_null_pct:.1f}% exceeds threshold {max_null_pct}%",
                    expected=f"max {max_null_pct}% nulls",
                    actual=f"{actual_null_pct:.1f}% nulls",
                )
            )

    actual_rows = output_table.num_rows
    for bound_name, bound_value, rule, cmp in (
        ("minimum", min_rows, "row_count_below_minimum", lambda a, b: a < b),
        ("maximum", max_rows, "row_count_above_maximum", lambda a, b: a > b),
    ):
        if bound_value is None:
            continue
        try:
            bound_int = int(bound_value)
        except (TypeError, ValueError):
            violations.append(
                ContractViolation(
                    column="__row_count__",
                    rule="invalid_row_bound",
                    severity="error",
                    message=(
                        f"Contract has a non-numeric row {bound_name}: "
                        f"{bound_value!r}"
                    ),
                    expected="integer row bound",
                    actual=str(bound_value),
                )
            )
            continue
        if cmp(actual_rows, bound_int):
            violations.append(
                ContractViolation(
                    column="__row_count__",
                    rule=rule,
                    severity="error",
                    message=f"Output has {actual_rows} rows, {bound_name} is {bound_value}",
                    expected=str(bound_value),
                    actual=str(actual_rows),
                )
            )

    unexpected = actual_columns - promised_cols
    for col_name in sorted(unexpected):
        violations.append(
            ContractViolation(
                column=col_name,
                rule="unexpected_column",
                severity="warning",
                message=f"Column '{col_name}' not defined in contract",
            )
        )

    return ContractValidationResult(
        passed=len(violations) == 0,
        violations=violations,
    )
