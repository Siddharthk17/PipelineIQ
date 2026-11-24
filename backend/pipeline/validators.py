"""Data quality validation rules engine for the validate step type.

Supports 12 check types that can be applied to DataFrame columns.
Each rule produces a ValidationRuleResult with pass/fail, counts,
and sample failing values.
"""

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class ValidationRuleResult:
    """Result of evaluating a single validation rule."""

    rule_name: str
    column: str | None
    check: str
    passed: bool
    severity: str
    failing_count: int
    total_count: int
    failing_examples: list
    message: str


@dataclass
class ValidationStepResult:
    """Aggregated result of all validation rules for a step."""

    passed: bool  # False only if error-severity rules fail
    error_count: int
    warning_count: int
    rule_results: list[ValidationRuleResult]
    output_df: pd.DataFrame  # passed through unchanged


SUPPORTED_CHECKS = frozenset({
    "not_null", "not_empty", "greater_than", "less_than", "between",
    "in_values", "matches_pattern", "no_duplicates", "min_rows",
    "max_rows", "date_format", "positive",
})


def execute_validate(
    df: pd.DataFrame,
    rules: list[dict],
    step_name: str,
) -> ValidationStepResult:
    """Execute all validation rules against a DataFrame."""
    results = [_execute_single_rule(df, rule) for rule in rules]
    error_count = sum(
        1 for r in results if not r.passed and r.severity == "error")
    warning_count = sum(
        1 for r in results if not r.passed and r.severity == "warning")
    return ValidationStepResult(
        passed=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        rule_results=results,
        output_df=df,
    )


def _execute_single_rule(df: pd.DataFrame, rule: Any) -> ValidationRuleResult:
    """Evaluate a single validation rule against a DataFrame.

    M-113: never raises. Malformed rules (non-mapping, missing keys,
    wrong-typed params) used to propagate KeyError/TypeError out of the
    run path as HTTP 500s. They are reported as failed error-severity
    results instead, so the run completes with actionable findings.
    """
    if not isinstance(rule, dict):
        return ValidationRuleResult(
            rule_name=f"rule.{type(rule).__name__}", column=None,
            check="invalid", passed=False, severity="error",
            failing_count=len(df), total_count=len(df), failing_examples=[],
            message=f"Validation rule must be a mapping; got {type(rule).__name__}",
        )
    check = rule.get("check")
    column = rule.get("column")
    severity = rule.get("severity", "error")
    if not isinstance(severity, str) or severity not in ("error", "warning"):
        severity = "error"
    if not isinstance(check, str) or not check:
        return ValidationRuleResult(
            rule_name=f"{column or 'dataset'}.invalid", column=column,
            check=str(check), passed=False, severity=severity,
            failing_count=len(df), total_count=len(df), failing_examples=[],
            message="Validation rule is missing required parameter 'check'",
        )

    try:
        return _evaluate_rule(df, rule, check, column, severity)
    except Exception as exc:
        return ValidationRuleResult(
            rule_name=f"{column or 'dataset'}.{check}", column=column,
            check=check, passed=False, severity=severity,
            failing_count=len(df), total_count=len(df), failing_examples=[],
            message=f"Rule evaluation failed: {exc}",
        )


# M-113: params each check needs. Missing params are reported as failed
# results here (runtime safety net); the parser rejects them earlier
# with step context at validation time.
_RULE_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "greater_than": ("value",),
    "less_than": ("value",),
    "between": ("min", "max"),
    "in_values": ("values",),
    "matches_pattern": ("pattern",),
    "min_rows": ("value",),
    "max_rows": ("value",),
    "date_format": ("format",),
}


def _evaluate_rule(
    df: pd.DataFrame, rule: dict, check: str, column: Any, severity: str
) -> ValidationRuleResult:
    """Evaluate a well-formed validation rule (may raise; caller guards)."""

    # Validate column exists
    if column and column not in df.columns:
        return ValidationRuleResult(
            rule_name=f"{column}.{check}", column=column, check=check,
            passed=False, severity=severity, failing_count=len(df),
            total_count=len(df), failing_examples=[],
            message=f"Column '{column}' not found",
        )

    series: Any = df[column] if column else None
    if series is None and check not in ("min_rows", "max_rows"):
        return ValidationRuleResult(
            rule_name=f"{column or 'dataset'}.{check}", column=column,
            check=check, passed=False, severity=severity,
            failing_count=len(df), total_count=len(df), failing_examples=[],
            message=f"Check '{check}' requires a column",
        )

    for param in _RULE_REQUIRED_PARAMS.get(check, ()):
        if param not in rule or rule[param] is None:
            return ValidationRuleResult(
                rule_name=f"{column or 'dataset'}.{check}", column=column,
                check=check, passed=False, severity=severity,
                failing_count=len(df), total_count=len(df),
                failing_examples=[],
                message=(f"Check '{check}' is missing required "
                         f"parameter '{param}'"),
            )

    if check == "not_null":
        mask = series.isnull()
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} null values in '{column}'"

    elif check == "not_empty":
        # C2-004: NULLs must fail the emptiness gate. astype(str) renders
        # None->'None'/NaN->'nan' (non-empty strings), so nulls previously
        # slipped through; OR the null mask in explicitly.
        mask = series.isnull() | (series.astype(str).str.strip() == "")
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} empty values in '{column}'"

    elif check == "greater_than":
        value = rule["value"]
        numeric = pd.to_numeric(series, errors="coerce")
        # B2: fail closed on garbage. pd.to_numeric(coerce) maps
        # non-numeric strings to NaN, and NaN comparisons are False, so
        # garbage rows previously never counted as failing. Non-null
        # values that do not parse as numbers are violations; genuine
        # NULLs keep the old pass-through (not_null owns them).
        unparseable = numeric.isna() & series.notna()
        mask = (numeric <= value) | unparseable
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} values in '{column}' not greater than {value}"

    elif check == "less_than":
        value = rule["value"]
        numeric = pd.to_numeric(series, errors="coerce")
        # B2: fail closed on garbage (see greater_than).
        unparseable = numeric.isna() & series.notna()
        mask = (numeric >= value) | unparseable
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} values in '{column}' not less than {value}"

    elif check == "between":
        min_val = rule["min"]
        max_val = rule["max"]
        numeric = pd.to_numeric(series, errors="coerce")
        # B2: fail closed on garbage (see greater_than).
        unparseable = numeric.isna() & series.notna()
        mask = (numeric < min_val) | (numeric > max_val) | unparseable
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} values in '{column}' not between {min_val} and {max_val}"

    elif check == "in_values":
        values = rule["values"]
        mask = ~series.isin(values)
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} values in '{column}' not in allowed list"

    elif check == "matches_pattern":
        pattern = rule["pattern"]
        try:
            # S2-008: reject catastrophic-backtracking shapes before
            # evaluating per value on workers (same guard as the contract
            # layers; invalid/nested patterns fail closed, never burn CPU).
            from backend.contracts.validator import _has_nested_quantifier

            if _has_nested_quantifier(str(pattern)):
                return ValidationRuleResult(
                    rule_name=f"{column}.{check}", column=column, check=check,
                    passed=False, severity=severity, failing_count=0,
                    total_count=len(df), failing_examples=[],
                    message="Regex pattern has nested quantifiers "
                    "(ReDoS risk); simplify the pattern.",
                )
            mask = ~series.astype(str).str.match(re.compile(pattern))
            count = int(mask.sum())
            examples = series[mask].head(3).tolist()
            passed = count == 0
            msg = f"{count} values in '{column}' don't match pattern"
        except re.error as e:
            return ValidationRuleResult(
                rule_name=f"{column}.{check}", column=column, check=check,
                passed=False, severity=severity, failing_count=0,
                total_count=len(df), failing_examples=[],
                message=f"Invalid regex: {e}",
            )

    elif check == "no_duplicates":
        count = int(series.duplicated().sum())
        examples = series[series.duplicated()].head(3).tolist()
        passed = count == 0
        msg = f"{count} duplicate values in '{column}'"

    elif check == "min_rows":
        value = rule["value"]
        passed = len(df) >= value
        count = 0 if passed else value - len(df)
        examples = []
        msg = f"Dataset has {len(df)} rows, minimum is {value}"

    elif check == "max_rows":
        value = rule["value"]
        passed = len(df) <= value
        count = 0 if passed else len(df) - value
        examples = []
        msg = f"Dataset has {len(df)} rows, maximum is {value}"

    elif check == "positive":
        numeric = pd.to_numeric(series, errors="coerce")
        mask = numeric <= 0
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} non-positive values in '{column}'"

    elif check == "date_format":
        fmt = rule["format"]
        parsed = pd.to_datetime(series, format=fmt, errors="coerce")
        mask = parsed.isnull() & series.notnull()
        count = int(mask.sum())
        examples = series[mask].head(3).tolist()
        passed = count == 0
        msg = f"{count} values in '{column}' don't match format {fmt}"

    else:
        return ValidationRuleResult(
            rule_name=f"{column or 'dataset'}.{check}", column=column,
            check=check, passed=False, severity=severity, failing_count=0,
            total_count=len(df), failing_examples=[],
            message=f"Unknown check: '{check}'",
        )

    return ValidationRuleResult(
        rule_name=f"{column or 'dataset'}.{check}", column=column,
        check=check, passed=passed, severity=severity,
        failing_count=count, total_count=len(df),
        failing_examples=examples, message=msg,
    )
