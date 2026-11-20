"""Data contracts for pre-execution schema validation.

A contract defines expected schema properties for a pipeline step's output:
required columns, allowed dtypes, value ranges, cardinality, and nullability.
Contracts are declared inline in YAML and evaluated before execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


class ContractSeverity(Enum):
    ERROR = "error"
    WARNING = "warning"


class DtypeKind(Enum):
    STRING = "string"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    ANY = "any"


DTYPE_MAP: dict[str, DtypeKind] = {
    "object": DtypeKind.STRING,
    "string": DtypeKind.STRING,
    "str": DtypeKind.STRING,
    "large_string": DtypeKind.STRING,
    "utf8": DtypeKind.STRING,
    "large_utf8": DtypeKind.STRING,
    "int64": DtypeKind.INTEGER,
    "Int64": DtypeKind.INTEGER,
    "int32": DtypeKind.INTEGER,
    "Int32": DtypeKind.INTEGER,
    # C2-002: the map previously covered only a handful of dtypes; any
    # other (int8, UInt*, Float32, pandas-2 str/boolean, ...) silently
    # passed validation. Enumerate the common variants; anything still
    # unmapped fails closed in validate() below.
    "int8": DtypeKind.INTEGER,
    "Int8": DtypeKind.INTEGER,
    "int16": DtypeKind.INTEGER,
    "Int16": DtypeKind.INTEGER,
    "uint8": DtypeKind.INTEGER,
    "UInt8": DtypeKind.INTEGER,
    "uint16": DtypeKind.INTEGER,
    "UInt16": DtypeKind.INTEGER,
    "uint32": DtypeKind.INTEGER,
    "UInt32": DtypeKind.INTEGER,
    "uint64": DtypeKind.INTEGER,
    "UInt64": DtypeKind.INTEGER,
    "float64": DtypeKind.FLOAT,
    "Float64": DtypeKind.FLOAT,
    "float32": DtypeKind.FLOAT,
    "Float32": DtypeKind.FLOAT,
    "float16": DtypeKind.FLOAT,
    "bool": DtypeKind.BOOLEAN,
    "boolean": DtypeKind.BOOLEAN,
    "bool_": DtypeKind.BOOLEAN,
    "datetime64[ns]": DtypeKind.DATETIME,
    "datetime64[us]": DtypeKind.DATETIME,
    "datetime64[ms]": DtypeKind.DATETIME,
    "datetime64[s]": DtypeKind.DATETIME,
    "datetime64": DtypeKind.DATETIME,
}


@dataclass
class ColumnContract:
    name: str
    dtype: DtypeKind | None = None
    required: bool = True
    not_null: bool = False
    unique: bool = False
    min_value: int | float | None = None
    max_value: int | float | None = None
    allowed_values: list[Any] | None = None
    regex: str | None = None

    def validate(self, series: pd.Series) -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        if self.dtype and self.dtype is not DtypeKind.ANY:
            actual_dtype = str(series.dtype)
            expected = DTYPE_MAP.get(actual_dtype)
            if expected is None:
                # C2-002: fail closed on unmapped dtypes. The old code
                # skipped the check when the dtype was absent from the map,
                # so int8/category/timedelta columns passed any contract.
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="dtype",
                        severity=ContractSeverity.ERROR,
                        message=(
                            f"Unmapped dtype '{actual_dtype}' cannot verify "
                            f"expected {self.dtype.value}"
                        ),
                        actual=actual_dtype,
                        expected=self.dtype.value,
                    )
                )
            elif expected is not self.dtype:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="dtype",
                        severity=ContractSeverity.ERROR,
                        message=f"Expected dtype {self.dtype.value}, got {actual_dtype}",
                        actual=actual_dtype,
                        expected=self.dtype.value,
                    )
                )

        if self.not_null:
            null_count = int(series.isna().sum())
            if null_count > 0:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="not_null",
                        severity=ContractSeverity.ERROR,
                        message=f"Column has {null_count} null values",
                        actual=str(null_count),
                        expected="0",
                    )
                )

        if self.unique:
            non_na = series.dropna()
            dup_count = int(non_na.duplicated().sum())
            if dup_count > 0:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="unique",
                        severity=ContractSeverity.WARNING,
                        message=f"Column has {dup_count} duplicate values",
                        actual=str(dup_count),
                        expected="0",
                    )
                )

        if self.min_value is not None:
            violations.extend(
                self._check_bound(
                    series, "min_value", self.min_value, "below", ">="))

        if self.max_value is not None:
            violations.extend(
                self._check_bound(
                    series, "max_value", self.max_value, "above", "<="))

        if self.allowed_values is not None:
            non_na = series.dropna()
            bad = non_na[~non_na.isin(self.allowed_values)]
            if len(bad) > 0:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="allowed_values",
                        severity=ContractSeverity.ERROR,
                        message=f"{len(bad)} rows with values not in allowed set",
                        actual=str(bad.unique().tolist()),
                        expected=str(self.allowed_values),
                    )
                )

        if self.regex is not None:
            violations.extend(self._check_regex(series))

        return violations

    def _check_bound(self, series: pd.Series, rule: str, bound,
                     direction: str, expected_op: str,
                     ) -> list[ContractViolation]:
        """Enforce min/max across dtypes (C2-003).

        The old ``kind in "iuf"`` gate silently skipped bool, datetime
        and every other kind. Numeric/bool compare directly; datetimes
        coerce the bound via ``pd.to_datetime``; anything else (or an
        uncoercible bound) fails closed so a misconfigured contract
        surfaces instead of passing silently.
        """
        import pandas as pd

        kind = series.dtype.kind
        clean = series.dropna()
        try:
            if kind in "iufb":
                bad = int(((clean < bound) if "min" in rule
                           else (clean > bound)).sum())
            elif kind in "mM":
                stamp = pd.to_datetime(bound)
                bad = int(((clean < stamp) if "min" in rule
                           else (clean > stamp)).sum())
            else:
                return [ContractViolation(
                    column=self.name,
                    rule=rule,
                    severity=ContractSeverity.ERROR,
                    message=(f"Cannot enforce {rule}={bound} on "
                             f"dtype '{series.dtype}'"),
                    actual=str(series.dtype),
                    expected=f"{expected_op} {bound}",
                )]
        except Exception as exc:
            return [ContractViolation(
                column=self.name,
                rule=rule,
                severity=ContractSeverity.ERROR,
                message=f"Cannot enforce {rule}={bound}: {exc}",
                actual=str(series.dtype),
                expected=f"{expected_op} {bound}",
            )]
        if bad > 0:
            return [ContractViolation(
                column=self.name,
                rule=rule,
                severity=ContractSeverity.ERROR,
                message=f"{bad} rows {direction} {rule} {bound}",
                actual=str(bad),
                expected=f"{expected_op} {bound}",
            )]
        return []

    def _check_regex(self, series: pd.Series) -> list[ContractViolation]:
        """Evaluate the ``regex`` pattern (C2-001, fail-closed).

        The pattern was parsed and stored but ``validate()`` never
        referenced it, so pattern contracts enforced nothing. Invalid
        patterns are config errors (ERROR, not silent pass); matching
        runs per value over the stringified non-null cells.
        """
        import re

        from backend.contracts.validator import _has_nested_quantifier

        if _has_nested_quantifier(self.regex or ""):
            return [ContractViolation(
                column=self.name,
                rule="regex",
                severity=ContractSeverity.ERROR,
                message=(
                    f"Regex pattern {self.regex!r} has nested quantifiers "
                    "(ReDoS risk); simplify the pattern"
                ),
                actual=self.regex,
                expected="ReDoS-safe regex",
            )]
        try:
            rx = re.compile(self.regex or "")
        except re.error as exc:
            return [ContractViolation(
                column=self.name,
                rule="regex",
                severity=ContractSeverity.ERROR,
                message=f"Invalid regex pattern {self.regex!r}: {exc}",
                actual=self.regex,
                expected="valid regex",
            )]
        non_na = series.dropna().astype(str)
        bad = int((~non_na.str.match(rx, na=False)).sum())
        if bad > 0:
            return [ContractViolation(
                column=self.name,
                rule="regex",
                severity=ContractSeverity.ERROR,
                message=(f"{bad} rows do not match pattern {self.regex!r}"),
                actual=str(bad),
                expected=f"match {self.regex}",
            )]
        return []


@dataclass
class ContractViolation:
    column: str
    rule: str
    severity: ContractSeverity
    message: str
    actual: str | None = None
    expected: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "rule": self.rule,
            "severity": self.severity.value,
            "message": self.message,
            "actual": self.actual,
            "expected": self.expected,
        }


@dataclass
class Contract:
    """A data contract for a pipeline step's expected output."""

    step_name: str
    columns: list[ColumnContract] = field(default_factory=list)
    min_rows: int | None = None
    max_rows: int | None = None

    def validate(self, df: pd.DataFrame) -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        for col_contract in self.columns:
            if col_contract.required and col_contract.name not in df.columns:
                violations.append(
                    ContractViolation(
                        column=col_contract.name,
                        rule="required",
                        severity=ContractSeverity.ERROR,
                        message=f"Required column '{col_contract.name}' is missing",
                    )
                )
                continue

            if col_contract.name not in df.columns:
                continue

            violations.extend(col_contract.validate(df[col_contract.name]))

        if self.min_rows is not None and len(df) < self.min_rows:
            violations.append(
                ContractViolation(
                    column="__table__",
                    rule="min_rows",
                    severity=ContractSeverity.ERROR,
                    message=f"Expected at least {self.min_rows} rows, got {len(df)}",
                    actual=str(len(df)),
                    expected=str(self.min_rows),
                )
            )

        if self.max_rows is not None and len(df) > self.max_rows:
            violations.append(
                ContractViolation(
                    column="__table__",
                    rule="max_rows",
                    severity=ContractSeverity.WARNING,
                    message=f"Expected at most {self.max_rows} rows, got {len(df)}",
                    actual=str(len(df)),
                    expected=str(self.max_rows),
                )
            )

        return violations


def parse_contract_from_yaml(data: dict) -> Contract:
    """Parse a contract dict from pipeline YAML into a Contract object.

    M-113: raises ValueError with a precise message on malformed
    contracts instead of leaking KeyError/ValueError internals.
    (The pipeline validator rejects these earlier with step context;
    this is the runtime safety net.)
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"Contract must be a mapping; got {type(data).__name__}")
    step_name = data.get("step_name", "")
    columns_data = data.get("columns", [])
    if not isinstance(columns_data, list):
        raise ValueError("Contract 'columns' must be a list")
    columns = []

    for idx, col in enumerate(columns_data):
        if not isinstance(col, dict):
            raise ValueError(
                f"Contract column #{idx} must be a mapping; "
                f"got {type(col).__name__}")
        if not isinstance(col.get("name"), str) or not col["name"]:
            raise ValueError(
                f"Contract column #{idx} must have a non-empty "
                f"string 'name'")
        dtype_raw = col.get("dtype")
        try:
            dtype = DtypeKind(dtype_raw) if dtype_raw else None
        except ValueError as exc:
            raise ValueError(
                f"Contract column '{col['name']}' has unknown dtype "
                f"'{dtype_raw}'. Valid: "
                f"{sorted(d.value for d in DtypeKind)}") from exc
        columns.append(
            ColumnContract(
                name=col["name"],
                dtype=dtype,
                required=col.get("required", True),
                not_null=col.get("not_null", False),
                unique=col.get("unique", False),
                min_value=col.get("min_value"),
                max_value=col.get("max_value"),
                allowed_values=col.get("allowed_values"),
                regex=col.get("regex"),
            )
        )

    return Contract(
        step_name=step_name,
        columns=columns,
        min_rows=data.get("min_rows"),
        max_rows=data.get("max_rows"),
    )


def validate_step_contract(
    contract_cfg: dict | None,
    df: pd.DataFrame,
    step_name: str,
) -> list[ContractViolation]:
    if not contract_cfg:
        return []
    # Copy: callers may pass a shared/cached mapping that must not be mutated.
    contract_cfg = dict(contract_cfg)
    contract_cfg["step_name"] = step_name
    contract = parse_contract_from_yaml(contract_cfg)
    return contract.validate(df)
