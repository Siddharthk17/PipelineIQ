"""Data contracts for pre-execution schema validation.

A contract defines expected schema properties for a pipeline step's output:
required columns, allowed dtypes, value ranges, cardinality, and nullability.
Contracts are declared inline in YAML and evaluated before execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

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
    "int64": DtypeKind.INTEGER,
    "Int64": DtypeKind.INTEGER,
    "int32": DtypeKind.INTEGER,
    "Int32": DtypeKind.INTEGER,
    "float64": DtypeKind.FLOAT,
    "Float64": DtypeKind.FLOAT,
    "float32": DtypeKind.FLOAT,
    "bool": DtypeKind.BOOLEAN,
    "boolean": DtypeKind.BOOLEAN,
    "datetime64[ns]": DtypeKind.DATETIME,
    "datetime64[us]": DtypeKind.DATETIME,
    "datetime64[ms]": DtypeKind.DATETIME,
}


@dataclass
class ColumnContract:
    name: str
    dtype: Optional[DtypeKind] = None
    required: bool = True
    not_null: bool = False
    unique: bool = False
    min_value: Optional[Union[int, float]] = None
    max_value: Optional[Union[int, float]] = None
    allowed_values: Optional[list[Any]] = None
    regex: Optional[str] = None

    def validate(self, series: pd.Series) -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        if self.dtype and self.dtype is not DtypeKind.ANY:
            actual_dtype = str(series.dtype)
            expected = DTYPE_MAP.get(actual_dtype)
            if expected and expected is not self.dtype:
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

        if self.min_value is not None and series.dtype.kind in "iuf":
            below = int((series.dropna() < self.min_value).sum())
            if below > 0:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="min_value",
                        severity=ContractSeverity.ERROR,
                        message=f"{below} rows below minimum value {self.min_value}",
                        actual=str(below),
                        expected=f">= {self.min_value}",
                    )
                )

        if self.max_value is not None and series.dtype.kind in "iuf":
            above = int((series.dropna() > self.max_value).sum())
            if above > 0:
                violations.append(
                    ContractViolation(
                        column=self.name,
                        rule="max_value",
                        severity=ContractSeverity.ERROR,
                        message=f"{above} rows above maximum value {self.max_value}",
                        actual=str(above),
                        expected=f"<= {self.max_value}",
                    )
                )

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

        return violations


@dataclass
class ContractViolation:
    column: str
    rule: str
    severity: ContractSeverity
    message: str
    actual: Optional[str] = None
    expected: Optional[str] = None

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
    min_rows: Optional[int] = None
    max_rows: Optional[int] = None

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
    """Parse a contract dict from pipeline YAML into a Contract object."""
    step_name = data.get("step_name", "")
    columns_data = data.get("columns", [])
    columns = []

    for col in columns_data:
        dtype_raw = col.get("dtype")
        dtype = DtypeKind(dtype_raw) if dtype_raw else None
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
    contract_cfg: Optional[dict],
    df: pd.DataFrame,
    step_name: str,
) -> list[ContractViolation]:
    if not contract_cfg:
        return []
    contract_cfg["step_name"] = step_name
    contract = parse_contract_from_yaml(contract_cfg)
    return contract.validate(df)
