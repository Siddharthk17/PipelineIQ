"""Pipeline execution orchestrator.

The PipelineRunner receives a parsed PipelineConfig, executes each step
in sequence via StepExecutor, records lineage via LineageRecorder, and
emits progress events via a dependency-injected callback. It does not
know how progress is reported (Redis pub/sub, SSE, logs) — pure
business logic with no infrastructure coupling.
"""

# Standard library
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

# Third-party packages
import pandas as pd

# Internal modules
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


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS & DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════


class PipelineStatus(str, Enum):
    """Lifecycle states for a pipeline run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    """Lifecycle states for an individual pipeline step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════


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
        self._executor = StepExecutor()

    def execute(
        self,
        config: PipelineConfig,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
        run_id: Optional[str] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> PipelineExecutionSummary:
        """Execute a complete pipeline from a parsed configuration.

        Args:
            config: Parsed and validated PipelineConfig.
            file_paths: Mapping of file_id → storage path for load steps.
            file_metadata: Mapping of file_id → metadata dict for load steps.
            run_id: Unique run identifier. Generated if not provided.
            progress_callback: Optional callback for progress events.
                Follows dependency inversion — the runner doesn't know
                how progress is reported.

        Returns:
            PipelineExecutionSummary with all step results, lineage,
            timing, and error information.
        """
        run_id = run_id or str(uuid.uuid4())
        callback = progress_callback or _noop_progress_callback
        recorder = LineageRecorder()
        df_registry: Dict[str, pd.DataFrame] = {}
        step_results: List[StepExecutionResult] = []
        total_rows_processed: int = 0
        pipeline_start = time.perf_counter()

        logger.info(
            "Pipeline '%s' (run_id=%s) starting with %d steps",
            config.name, run_id, len(config.steps),
        )

        try:
            for index, step in enumerate(config.steps):
                result = self._execute_single_step(
                    step=step,
                    index=index,
                    total_steps=len(config.steps),
                    run_id=run_id,
                    df_registry=df_registry,
                    recorder=recorder,
                    file_paths=file_paths,
                    file_metadata=file_metadata,
                    callback=callback,
                )
                step_results.append(result)
                df_registry[step.name] = result.output_df
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
            config.name, run_id, format_duration(total_duration),
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
        df_registry: Dict[str, pd.DataFrame],
        recorder: LineageRecorder,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
        callback: ProgressCallback,
    ) -> StepExecutionResult:
        """Execute a single step with progress event emission."""
        callback(StepProgressEvent(
            run_id=run_id,
            step_name=step.name,
            step_index=index,
            total_steps=total_steps,
            status=StepStatus.RUNNING,
        ))

        logger.info(
            "Executing step %d/%d: '%s' (type=%s)",
            index + 1, total_steps, step.name, step.step_type,
        )

        try:
            result = self._executor.execute(
                df_registry=df_registry,
                config=step,
                recorder=recorder,
                file_paths=file_paths,
                file_metadata=file_metadata,
            )
        except StepExecutionError as exc:
            callback(StepProgressEvent(
                run_id=run_id,
                step_name=step.name,
                step_index=index,
                total_steps=total_steps,
                status=StepStatus.FAILED,
                error_message=str(exc),
            ))
            raise

        callback(StepProgressEvent(
            run_id=run_id,
            step_name=step.name,
            step_index=index,
            total_steps=total_steps,
            status=StepStatus.COMPLETED,
            rows_in=result.rows_in,
            rows_out=result.rows_out,
            duration_ms=result.duration_ms,
        ))

        logger.info(
            "Step '%s' completed: %d → %d rows in %dms",
            step.name, result.rows_in, result.rows_out, result.duration_ms,
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
            config.name, run_id, error,
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
