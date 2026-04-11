"""Pipeline execution orchestrator.

The PipelineRunner receives a parsed PipelineConfig, executes each step
in sequence via StepExecutor, records lineage via LineageRecorder, and
emits progress events via a dependency-injected callback. It does not
know how progress is reported (Redis pub/sub, SSE, logs) — pure
business logic with no infrastructure coupling.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

import pandas as pd
import pyarrow as pa

from backend.execution.arrow_bus import get_arrow_bus, ArrowDataBus
from backend.execution.duckdb_executor import DuckDBExecutor
from backend.execution.smart_executor import SmartExecutor
from backend.pipeline.exceptions import StepExecutionError
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    LoadStepConfig,
    PipelineConfig,
    StepConfig,
)
from backend.pipeline.steps import StepExecutionResult, StepExecutor
from backend.utils.time_utils import format_duration, measure_ms

logger = logging.getLogger(__name__)


class PipelineStatus(str, Enum):
    """Lifecycle states for a pipeline run (internal to runner)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(str, Enum):
    """Lifecycle states for an individual pipeline step (internal to runner)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


@dataclass
class StepProgressEvent:
    """Event emitted during pipeline execution for progress tracking."""

    run_id: str
    step_name: str
    step_index: int
    total_steps: int
    status: StepStatus
    rows_in: Optional[int] = None
    rows_out: Optional[int] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None


# Callback type for progress reporting — dependency inversion
ProgressCallback = Callable[[StepProgressEvent], None]


@dataclass
class PipelineExecutionSummary:
    """Complete summary of a pipeline execution."""

    run_id: str
    pipeline_name: str
    status: PipelineStatus
    step_results: List[StepExecutionResult]
    lineage: LineageRecorder
    total_duration_ms: int
    total_rows_processed: int
    error: Optional[StepExecutionError] = None


def _noop_progress_callback(event: StepProgressEvent) -> None:
    """Default no-op progress callback used when none is provided."""
    pass


class PipelineRunner:
    """Orchestrates the execution of a complete pipeline.

    Responsibilities:
    1. Receives a parsed PipelineConfig (NOT raw YAML)
    2. Maintains a df_registry mapping step name → output DataFrame
    3. Calls StepExecutor for each step
    4. Calls LineageRecorder after each step (via StepExecutor)
    5. Emits progress events via a callback
    6. Collects StepExecutionResults
    7. Returns a PipelineExecutionSummary
    """

    def __init__(self) -> None:
        self._pandas_executor = StepExecutor()
        self._duckdb_executor = DuckDBExecutor()
        self._executor = SmartExecutor(
            pandas_executor=self._pandas_executor,
            duckdb_executor=self._duckdb_executor,
        )

    def execute(
        self,
        config: PipelineConfig,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
        run_id: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> PipelineExecutionSummary:
        """Execute a complete pipeline from a parsed configuration."""
        run_id = run_id or str(uuid.uuid4())
        callback = progress_callback or _noop_progress_callback
        recorder = LineageRecorder()
        bus = get_arrow_bus()
        table_registry: Dict[str, pa.Table] = {}
        step_results: List[StepExecutionResult] = []
        total_rows_processed: int = 0
        pipeline_start = time.perf_counter()

        logger.info(
            "Pipeline '%s' (run_id=%s) starting with %d steps",
            config.name,
            run_id,
            len(config.steps),
        )

        try:
            for index, step in enumerate(config.steps):
                result = self._execute_single_step(
                    step=step,
                    index=index,
                    total_steps=len(config.steps),
                    run_id=run_id,
                    table_registry=table_registry,
                    recorder=recorder,
                    file_paths=file_paths,
                    file_metadata=file_metadata,
                    callback=callback,
                    bus=bus,
                )
                step_results.append(result)
                table_registry[step.name] = result.output_table
                total_rows_processed += result.rows_in

        except StepExecutionError as exc:
            return self._build_failed_summary(
                run_id=run_id,
                config=config,
                step_results=step_results,
                recorder=recorder,
                total_rows_processed=total_rows_processed,
                total_duration_ms=measure_ms(pipeline_start),
                error=exc,
            )

        total_duration = measure_ms(pipeline_start)
        logger.info(
            "Pipeline '%s' (run_id=%s) completed in %s",
            config.name,
            run_id,
            format_duration(total_duration),
        )

        return PipelineExecutionSummary(
            run_id=run_id,
            pipeline_name=config.name,
            status=PipelineStatus.COMPLETED,
            step_results=step_results,
            lineage=recorder,
            total_duration_ms=total_duration,
            total_rows_processed=total_rows_processed,
        )

    def _execute_single_step(
        self,
        step: StepConfig,
        index: int,
        total_steps: int,
        run_id: str,
        table_registry: Dict[str, pa.Table],
        recorder: LineageRecorder,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
        callback: ProgressCallback,
        bus: ArrowDataBus,
    ) -> StepExecutionResult:
        """Execute a single step with progress event emission."""
        callback(
            StepProgressEvent(
                run_id=run_id,
                step_name=step.name,
                step_index=index,
                total_steps=total_steps,
                status=StepStatus.RUNNING,
            )
        )

        logger.info(
            "Executing step %d/%d: '%s' (type=%s)",
            index + 1,
            total_steps,
            step.name,
            step.step_type,
        )

        try:
            result = self._executor.execute_step(
                step=step,
                table_registry=table_registry,
                recorder=recorder,
                file_paths=file_paths,
                file_metadata=file_metadata,
            )
            # Persist result to Arrow data bus (tiered storage)
            bus.put(key=step.name, table=result.output_table, run_id=run_id)
        except StepExecutionError as exc:
            callback(
                StepProgressEvent(
                    run_id=run_id,
                    step_name=step.name,
                    step_index=index,
                    total_steps=total_steps,
                    status=StepStatus.FAILED,
                    error_message=str(exc),
                )
            )
            raise

        callback(
            StepProgressEvent(
                run_id=run_id,
                step_name=step.name,
                step_index=index,
                total_steps=total_steps,
                status=StepStatus.COMPLETED,
                rows_in=result.rows_in,
                rows_out=result.rows_out,
                duration_ms=result.duration_ms,
            )
        )

        logger.info(
            "Step '%s' completed: %d → %d rows in %dms",
            step.name,
            result.rows_in,
            result.rows_out,
            result.duration_ms,
        )

        return result

    def _build_failed_summary(
        self,
        run_id: str,
        config: PipelineConfig,
        step_results: List[StepExecutionResult],
        recorder: LineageRecorder,
        total_rows_processed: int,
        total_duration_ms: int,
        error: StepExecutionError,
    ) -> PipelineExecutionSummary:
        """Build a summary for a failed pipeline execution."""
        logger.error(
            "Pipeline '%s' (run_id=%s) failed: %s",
            config.name,
            run_id,
            error,
        )
        return PipelineExecutionSummary(
            run_id=run_id,
            pipeline_name=config.name,
            status=PipelineStatus.FAILED,
            step_results=step_results,
            lineage=recorder,
            total_duration_ms=total_duration_ms,
            total_rows_processed=total_rows_processed,
            error=error,
        )
