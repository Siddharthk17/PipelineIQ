"""Smart routing between Pandas and DuckDB for pipeline execution."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional, cast

import pyarrow as pa
from opentelemetry import trace

from backend.execution.duckdb_executor import DuckDBExecutor
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import LoadStepConfig
from backend.pipeline.steps import StepConfig, StepExecutionResult, StepExecutor
from backend.telemetry import current_span_context, get_tracer

logger = logging.getLogger(__name__)

DUCKDB_THRESHOLD = 50_000
DUCKDB_CAPABLE_STEPS = frozenset(
    {
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
        "sql",
    }
)
ALWAYS_PANDAS_STEPS = frozenset({"load", "save", "validate", "rename", "wasm_compute"})


class SmartExecutor:
    """Route compatible steps to DuckDB when inputs are large."""

    DUCKDB_THRESHOLD = DUCKDB_THRESHOLD
    DUCKDB_CAPABLE_STEPS = DUCKDB_CAPABLE_STEPS
    _DUCKDB_COMPATIBLE_STEPS = DUCKDB_CAPABLE_STEPS
    _ALWAYS_PANDAS_STEPS = ALWAYS_PANDAS_STEPS

    def __init__(
        self,
        pandas_executor: Optional[StepExecutor] = None,
        duckdb_executor: Optional[DuckDBExecutor] = None,
    ) -> None:
        self.pandas_executor = pandas_executor or StepExecutor()
        self.duckdb_executor = duckdb_executor or DuckDBExecutor()

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

    def _as_result(
        self,
        step: StepConfig,
        output_table: pa.Table,
        *,
        rows_in: int,
        columns_in: list[str],
        duration_ms: int,
        engine: str = "pandas",
        trace_id: str | None = None,
        span_id: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> StepExecutionResult:
        now = datetime.now(timezone.utc)
        return StepExecutionResult(
            step_name=step.name,
            step_type=SmartExecutor._step_type_label(step),
            output_table=output_table,
            rows_in=rows_in,
            rows_out=output_table.num_rows,
            columns_in=columns_in,
            columns_out=output_table.column_names,
            duration_ms=duration_ms,
            engine=engine,
            trace_id=trace_id,
            span_id=span_id,
            started_at=started_at or now,
            completed_at=completed_at or now,
        )

    @staticmethod
    def _enrich_result(
        result: StepExecutionResult,
        engine: str,
        span_ctx: dict,
        started_at: datetime | None = None,
    ) -> StepExecutionResult:
        """Attach OTel trace/span IDs, engine, and timestamps to an existing StepExecutionResult."""
        now = datetime.now(timezone.utc)
        result.engine = engine
        result.trace_id = span_ctx.get("trace_id")
        result.span_id = span_ctx.get("span_id")
        result.started_at = started_at or result.started_at or now
        result.completed_at = now
        return result

    def execute(
        self,
        step: StepConfig,
        table_registry: dict[str, pa.Table],
        recorder: LineageRecorder,
        *,
        file_paths: Optional[dict[str, str]] = None,
        file_metadata: Optional[dict[str, dict[str, str]]] = None,
        extra_tables: Optional[dict[str, pa.Table]] = None,
        wasm_modules: Optional[dict[str, bytes]] = None,
        user_role: Optional[str] = None,
    ) -> StepExecutionResult:
        """Execute a step using Pandas or DuckDB based on compatibility and size."""
        step_type = self._step_type(step)
        step_label = self._step_type_label(step)
        tracer = get_tracer()

        engine = "pandas"
        row_count_in = 0
        if step_type in self.DUCKDB_CAPABLE_STEPS:
            input_step_name = getattr(step, "input", None)
            input_table = table_registry.get(input_step_name) if input_step_name else None
            if input_table is not None and input_table.num_rows >= self.DUCKDB_THRESHOLD:
                engine = "duckdb"
                row_count_in = input_table.num_rows

        # Start the step span
        with tracer.start_as_current_span(
            name=f"step:{step.name}",
            attributes={
                "pipelineiq.step.name": step.name,
                "pipelineiq.step.type": step_label,
                "pipelineiq.step.engine": engine,
                "pipelineiq.rows.in": row_count_in,
            },
        ) as _step_span:
            span_ctx = current_span_context()
            step_started = datetime.now(timezone.utc)
            try:
                if step_type == "load":
                    result = self.pandas_executor.execute_load(
                        table_registry,
                        cast(LoadStepConfig, step),
                        recorder,
                        file_paths or {},
                        file_metadata or {},
                        user_role=user_role,
                    )
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_attribute("pipelineiq.columns_out", len(result.columns_out))
                    _step_span.set_status(trace.StatusCode.OK)
                    return self._enrich_result(result, "pandas", span_ctx, started_at=step_started)

                if step_type in self._ALWAYS_PANDAS_STEPS:
                    result = self.pandas_executor.execute(
                        table_registry, step, recorder, wasm_modules=wasm_modules
                    )
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_status(trace.StatusCode.OK)
                    return self._enrich_result(result, engine, span_ctx, started_at=step_started)

                input_step_name = getattr(step, "input", None)
                input_table = table_registry.get(
                    input_step_name) if input_step_name else None

                if step_type == "sql":
                    if input_table is None:
                        result = self.pandas_executor.execute(table_registry, step, recorder)
                        _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                        _step_span.set_status(trace.StatusCode.OK)
                        return self._enrich_result(result, engine, span_ctx, started_at=step_started)
                    logger.debug(
                        "Routing SQL step '%s' to DuckDB (rows=%d)",
                        step.name, input_table.num_rows,
                    )
                    start = time.perf_counter()
                    output_table = self.duckdb_executor.execute_step(
                        step, input_table, extra_tables=extra_tables
                    )
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    result = self._as_result(
                        step, output_table,
                        rows_in=input_table.num_rows,
                        columns_in=input_table.column_names,
                        duration_ms=duration_ms,
                        engine="duckdb",
                        trace_id=span_ctx.get("trace_id"),
                        span_id=span_ctx.get("span_id"),
                        started_at=step_started,
                    )
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_status(trace.StatusCode.OK)
                    return result

                if step_type == "join":
                    left_name = getattr(step, "left", None)
                    right_name = getattr(step, "right", None)
                    left_table = table_registry.get(left_name) if left_name else None
                    right_table = table_registry.get(right_name) if right_name else None
                    if left_table is None or right_table is None:
                        result = self.pandas_executor.execute(table_registry, step, recorder)
                        _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                        _step_span.set_status(trace.StatusCode.OK)
                        return self._enrich_result(result, engine, span_ctx, started_at=step_started)
                    if max(left_table.num_rows, right_table.num_rows) <= self.DUCKDB_THRESHOLD:
                        result = self.pandas_executor.execute(table_registry, step, recorder)
                        _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                        _step_span.set_status(trace.StatusCode.OK)
                        return self._enrich_result(result, engine, span_ctx, started_at=step_started)
                    logger.debug(
                        "Routing join step '%s' to DuckDB (left_rows=%d, right_rows=%d)",
                        step.name, left_table.num_rows, right_table.num_rows,
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
                    result = self._as_result(
                        step, output_table,
                        rows_in=max(left_table.num_rows, right_table.num_rows),
                        columns_in=columns_in,
                        duration_ms=duration_ms,
                        engine="duckdb",
                        trace_id=span_ctx.get("trace_id"),
                        span_id=span_ctx.get("span_id"),
                        started_at=step_started,
                    )
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_status(trace.StatusCode.OK)
                    return result

                if input_table is None:
                    result = self.pandas_executor.execute(table_registry, step, recorder)
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_status(trace.StatusCode.OK)
                    return self._enrich_result(result, engine, span_ctx, started_at=step_started)

                should_route_to_duckdb = (
                    step_type in self.DUCKDB_CAPABLE_STEPS
                    and input_table.num_rows >= self.DUCKDB_THRESHOLD
                )
                if not should_route_to_duckdb:
                    result = self.pandas_executor.execute(table_registry, step, recorder)
                    _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                    _step_span.set_status(trace.StatusCode.OK)
                    return self._enrich_result(result, engine, span_ctx, started_at=step_started)

                logger.debug(
                    "Routing step '%s' to DuckDB (type=%s, rows=%d)",
                    step.name, step_type, input_table.num_rows,
                )
                start = time.perf_counter()
                output_table = self.duckdb_executor.execute_step(
                    step, input_table, extra_tables=extra_tables
                )
                duration_ms = int((time.perf_counter() - start) * 1000)
                result = self._as_result(
                    step, output_table,
                    rows_in=input_table.num_rows,
                    columns_in=input_table.column_names,
                    duration_ms=duration_ms,
                    engine="duckdb",
                    trace_id=span_ctx.get("trace_id"),
                    span_id=span_ctx.get("span_id"),
                    started_at=step_started,
                )
                _step_span.set_attribute("pipelineiq.rows.out", result.rows_out)
                _step_span.set_status(trace.StatusCode.OK)
                return result

            except Exception as e:
                _step_span.record_exception(e)
                _step_span.set_status(
                    trace.StatusCode.ERROR,
                    description=str(e)[:500]
                )
                raise
