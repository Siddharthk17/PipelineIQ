"""Smart routing between Pandas and DuckDB execution paths."""

from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
import pyarrow as pa

from backend.execution.duckdb_executor import DuckDBExecutor

ROW_THRESHOLD_DEFAULT = 50_000

DUCKDB_CAPABLE_STEPS = frozenset(
    {
        "filter",
        "select",
        "join",
        "aggregate",
        "sort",
        "pivot",
        "unpivot",
        "deduplicate",
        "fill_nulls",
        "sample",
        "sql",
    }
)


class SmartExecutor:
    """Route eligible heavy steps to DuckDB; keep others on Pandas."""

    def __init__(
        self,
        *,
        row_threshold: int = ROW_THRESHOLD_DEFAULT,
        duckdb_executor: Optional[DuckDBExecutor] = None,
    ) -> None:
        self._row_threshold = max(1, row_threshold)
        self._duckdb = duckdb_executor or DuckDBExecutor()

    @staticmethod
    def _step_type_value(step: object) -> str:
        step_type = getattr(step, "step_type", getattr(step, "type", ""))
        if hasattr(step_type, "value"):
            return str(step_type.value)
        return str(step_type)

    def _is_duckdb_compatible(self, step: object) -> bool:
        step_type = self._step_type_value(step)
        if step_type == "sample" and getattr(step, "stratify_by", None):
            return False
        if step_type == "deduplicate":
            subset = getattr(step, "subset", None)
            keep = str(getattr(step, "keep", "first")).lower()
            if keep in {"none", "false"} and not subset:
                return False
        if step_type == "fill_nulls":
            columns = getattr(step, "columns", None) or []
            return len(columns) > 0
        return True

    def should_use_duckdb(self, step: object, row_count: int) -> bool:
        step_type = self._step_type_value(step)
        if step_type not in DUCKDB_CAPABLE_STEPS:
            return False
        if not self._is_duckdb_compatible(step):
            return False
        if step_type == "sql":
            return True
        return row_count >= self._row_threshold

    def execute_unary(
        self,
        step: object,
        input_df: pd.DataFrame,
        pandas_fallback: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        if not self.should_use_duckdb(step, len(input_df)):
            return pandas_fallback()
        input_table = pa.Table.from_pandas(input_df, preserve_index=False)
        output_table = self._duckdb.execute_step(step, input_table)
        return output_table.to_pandas()

    def execute_join(
        self,
        step: object,
        left_df: pd.DataFrame,
        right_df: pd.DataFrame,
        pandas_fallback: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        row_count = max(len(left_df), len(right_df))
        if not self.should_use_duckdb(step, row_count):
            return pandas_fallback()
        left_table = pa.Table.from_pandas(left_df, preserve_index=False)
        right_table = pa.Table.from_pandas(right_df, preserve_index=False)
        output_table = self._duckdb.execute_step(
            step,
            left_table,
            extra_tables={"__left__": left_table, "__right__": right_table},
        )
        return output_table.to_pandas()

    def execute_sql(self, step: object, input_df: pd.DataFrame) -> pd.DataFrame:
        input_table = pa.Table.from_pandas(input_df, preserve_index=False)
        output_table = self._duckdb.execute_step(step, input_table)
        return output_table.to_pandas()

