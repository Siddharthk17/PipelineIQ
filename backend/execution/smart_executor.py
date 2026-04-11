"""Smart routing between Pandas and DuckDB for pipeline execution."""

from __future__ import annotations

import logging
import time
from typing import Optional

import pyarrow as pa

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.pipeline.steps import StepConfig, StepExecutionResult, StepExecutor

logger = logging.getLogger(__name__)


class SmartExecutor:
    """Route compatible steps to DuckDB when inputs are large."""

    DUCKDB_THRESHOLD = 50_000
    _DUCKDB_COMPATIBLE_STEPS = {
        "filter",
        "select",
        "sort",
        "aggregate",
        "join",
        "deduplicate",
        "fill_nulls",
        "sample",
        "pivot",
        "unpivot",
    }
    _ALWAYS_PANDAS_STEPS = {"load", "save", "validate", "rename"}

    def __init__(
        self,
        pandas_executor: StepExecutor,
        duckdb_executor: DuckDBExecutor,
    ) -> None:
        self.pandas_executor = pandas_executor
        self.duckdb_executor = duckdb_executor

    @staticmethod
    def _step_type(step: StepConfig) -> str:
        step_type = getattr(step, "step_type", "")
        if hasattr(step_type, "value"):
            return str(step_type.value).lower()
        return str(step_type).lower()

    @staticmethod
    def _step_type_label(step: StepConfig) -> str:
        step_type = getattr(step, "step_type", "")
        if hasattr(step_type, "value"):
            return str(step_type.value)
        return str(step_type)

    @staticmethod
    def _as_result(
        step: StepConfig,
        output_table: pa.Table,
        *,
        rows_in: int,
        columns_in: list[str],
        duration_ms: int,
    ) -> StepExecutionResult:
        return StepExecutionResult(
            step_name=step.name,
            step_type=SmartExecutor._step_type_label(step),
            output_table=output_table,
            rows_in=rows_in,
            rows_out=output_table.num_rows,
            columns_in=columns_in,
            columns_out=output_table.column_names,
            duration_ms=duration_ms,
        )

    def execute_step(
        self,
        step: StepConfig,
        table_registry: dict[str, pa.Table],
        recorder: object,
        *,
        file_paths: Optional[dict[str, str]] = None,
        file_metadata: Optional[dict[str, dict[str, str]]] = None,
        extra_tables: Optional[dict[str, pa.Table]] = None,
    ) -> StepExecutionResult:
        """Execute a step using Pandas or DuckDB based on compatibility and size."""
        step_type = self._step_type(step)

        if step_type == "load":
            return self.pandas_executor.execute_load(
                table_registry, step, recorder, file_paths or {}, file_metadata or {}
            )

        if step_type in self._ALWAYS_PANDAS_STEPS:
            return self.pandas_executor.execute(table_registry, step, recorder)

        if step_type == "join":
            left_name = getattr(step, "left", None)
            right_name = getattr(step, "right", None)
            left_table = table_registry.get(left_name) if left_name else None
            right_table = table_registry.get(right_name) if right_name else None
            if left_table is None or right_table is None:
                return self.pandas_executor.execute(table_registry, step, recorder)

            if max(left_table.num_rows, right_table.num_rows) <= self.DUCKDB_THRESHOLD:
                return self.pandas_executor.execute(table_registry, step, recorder)

            logger.debug(
                "Routing join step '%s' to DuckDB (left_rows=%d, right_rows=%d)",
                step.name,
                left_table.num_rows,
                right_table.num_rows,
            )
            duckdb_tables = {"__left__": left_table, "__right__": right_table}
            if extra_tables:
                duckdb_tables.update(extra_tables)

            start = time.perf_counter()
            output_table = self.duckdb_executor.execute_step(
                step, left_table, extra_tables=duckdb_tables
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            columns_in = left_table.column_names + [
                col for col in right_table.column_names if col not in left_table.column_names
            ]
            return self._as_result(
                step,
                output_table,
                rows_in=max(left_table.num_rows, right_table.num_rows),
                columns_in=columns_in,
                duration_ms=duration_ms,
            )

        input_step_name = getattr(step, "input", None)
        input_table = table_registry.get(input_step_name) if input_step_name else None
        if input_table is None:
            return self.pandas_executor.execute(table_registry, step, recorder)

        should_route_to_duckdb = step_type == "sql" or (
            step_type in self._DUCKDB_COMPATIBLE_STEPS
            and input_table.num_rows > self.DUCKDB_THRESHOLD
        )
        if not should_route_to_duckdb:
            return self.pandas_executor.execute(table_registry, step, recorder)

        logger.debug(
            "Routing step '%s' to DuckDB (type=%s, rows=%d)",
            step.name,
            step_type,
            input_table.num_rows,
        )
        start = time.perf_counter()
        output_table = self.duckdb_executor.execute_step(
            step, input_table, extra_tables=extra_tables
        )
        duration_ms = int((time.perf_counter() - start) * 1000)
        return self._as_result(
            step,
            output_table,
            rows_in=input_table.num_rows,
            columns_in=input_table.column_names,
            duration_ms=duration_ms,
        )
