"""Data contract validation engine.

Parses YAML contract definitions and validates pipeline output
against promised column types, null thresholds, and row count bounds.

Extracted from contracts.__init__ for cleaner separation of concerns.
Contract validation logic belongs in a single module, not mixed with
API or pipeline-layer concerns.
"""

from __future__ import annotations

import logging
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


def _parse_contract(yaml_content: str) -> dict:
    """Parse contract YAML into a validated dict."""
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as exc:
        logger.error("Failed to parse contract YAML: %s", exc)
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
            field_idx = output_table.schema.get_field_index(col_name)
            actual_dtype = output_table.schema[field_idx].type
            actual_cat = _arrow_type_category(actual_dtype)
            expected_cat = TYPE_CATEGORY_MAP.get(expected_type.lower(), expected_type.lower())

            if actual_cat != expected_cat:
                violations.append(
                    ContractViolation(
                        column=col_name,
                        rule="type_changed",
                        severity="error",
                        message=f"Column '{col_name}' type mismatch: expected {expected_type}, got {actual_dtype}",
                        expected=expected_type,
                        actual=str(actual_dtype),
                    )
                )

        nullable = col_spec.get("nullable")
        if nullable is False:
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

        min_val = col_spec.get("min_value")
        max_val = col_spec.get("max_value")
        if min_val is not None or max_val is not None:
            field_idx = output_table.schema.get_field_index(col_name)
            col = output_table.column(field_idx)
            if min_val is not None:
                below = pc.less(col, min_val)
                below_count = pc.sum(below).as_py() or 0
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
                above = pc.greater(col, max_val)
                above_count = pc.sum(above).as_py() or 0
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
            unique_vals = pc.unique(col).to_pylist()
            allowed_set = set(str(v) for v in allowed)
            disallowed = [v for v in unique_vals if str(v) not in allowed_set]
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
            unique_count = pc.count_distinct(col).as_py() or 0
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

        field_idx = output_table.schema.get_field_index(col_name)
        col = output_table.column(field_idx)
        null_count = col.null_count
        total_count = len(col)
        if total_count == 0:
            continue

        actual_null_pct = (null_count / total_count) * 100
        if actual_null_pct > float(max_null_pct):
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
    if min_rows is not None and actual_rows < int(min_rows):
        violations.append(
            ContractViolation(
                column="__row_count__",
                rule="row_count_below_minimum",
                severity="error",
                message=f"Output has {actual_rows} rows, minimum is {min_rows}",
                expected=str(min_rows),
                actual=str(actual_rows),
            )
        )

    if max_rows is not None and actual_rows > int(max_rows):
        violations.append(
            ContractViolation(
                column="__row_count__",
                rule="row_count_above_maximum",
                severity="error",
                message=f"Output has {actual_rows} rows, maximum is {max_rows}",
                expected=str(max_rows),
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
