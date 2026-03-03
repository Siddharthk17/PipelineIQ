"""Pipeline step executor with dispatch-dict pattern.

Implements one public method per step type, dispatched via a clean
mapping instead of if-elif chains. Each method follows the same
signature pattern, returning a StepExecutionResult with timing,
row counts, and column metadata.
"""

# Standard library
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

# Third-party packages
import pandas as pd

# Internal modules
from backend.pipeline.exceptions import (
    AggregationError,
    ColumnNotFoundError,
    FileReadError,
    InvalidOperatorError,
    JoinKeyMissingError,
    UnsupportedFileFormatError,
)
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    AggregateStepConfig,
    FilterOperator,
    FilterStepConfig,
    JoinStepConfig,
    LoadStepConfig,
    RenameStepConfig,
    SaveStepConfig,
    SelectStepConfig,
    SortOrder,
    SortStepConfig,
    StepConfig,
    StepType,
)
from backend.utils.time_utils import measure_ms

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# STEP EXECUTION RESULT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class StepExecutionResult:
    """Result of executing a single pipeline step."""

    step_name: str
    step_type: str
    output_df: pd.DataFrame
    rows_in: int
    rows_out: int
    columns_in: List[str]
    columns_out: List[str]
    duration_ms: int
    warnings: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# FILTER OPERATOR DISPATCH
# ═══════════════════════════════════════════════════════════════════════════════

# Maps FilterOperator → a callable that takes (pd.Series, value) → pd.Series[bool]
FILTER_OPERATIONS: Dict[FilterOperator, Callable[[pd.Series, Any], pd.Series]] = {
    FilterOperator.EQUALS:                lambda s, v: s == v,
    FilterOperator.NOT_EQUALS:            lambda s, v: s != v,
    FilterOperator.GREATER_THAN:          lambda s, v: s > v,
    FilterOperator.LESS_THAN:             lambda s, v: s < v,
    FilterOperator.GREATER_THAN_OR_EQUAL: lambda s, v: s >= v,
    FilterOperator.LESS_THAN_OR_EQUAL:    lambda s, v: s <= v,
    FilterOperator.CONTAINS:              lambda s, v: s.astype(str).str.contains(str(v), na=False),
    FilterOperator.NOT_CONTAINS:          lambda s, v: ~s.astype(str).str.contains(str(v), na=False),
    FilterOperator.STARTS_WITH:           lambda s, v: s.astype(str).str.startswith(str(v), na=False),
    FilterOperator.ENDS_WITH:             lambda s, v: s.astype(str).str.endswith(str(v), na=False),
    FilterOperator.IS_NULL:               lambda s, _: s.isna(),
    FilterOperator.IS_NOT_NULL:           lambda s, _: s.notna(),
}

SUPPORTED_FILE_EXTENSIONS: Dict[str, Callable[..., pd.DataFrame]] = {
    ".csv": pd.read_csv,
    ".json": pd.read_json,
}


# ═══════════════════════════════════════════════════════════════════════════════
# STEP EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════


class StepExecutor:
    """Executes individual pipeline steps and records lineage.

    Uses a dispatch dictionary to map StepType enum values to executor
    methods, avoiding long if-elif chains. Each executor method follows
    the same pattern: validate inputs, execute transformation, record
    lineage, and return StepExecutionResult.
    """

    def __init__(self) -> None:
        self._dispatch: Dict[StepType, Callable] = {
            StepType.LOAD: self.execute_load,
            StepType.FILTER: self.execute_filter,
            StepType.SELECT: self.execute_select,
            StepType.RENAME: self.execute_rename,
            StepType.JOIN: self.execute_join,
            StepType.AGGREGATE: self.execute_aggregate,
            StepType.SORT: self.execute_sort,
            StepType.SAVE: self.execute_save,
        }

    def execute(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: StepConfig,
        recorder: LineageRecorder,
        file_paths: Optional[Dict[str, str]] = None,
        file_metadata: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> StepExecutionResult:
        """Dispatch step execution to the appropriate handler.

        Args:
            df_registry: Mapping of step name → output DataFrame.
            config: Typed step configuration.
            recorder: Active lineage recorder for this pipeline run.
            file_paths: Mapping of file_id → storage path (for load steps).
            file_metadata: Mapping of file_id → metadata dict (for load steps).

        Returns:
            StepExecutionResult with the output DataFrame and metadata.

        Raises:
            ValueError: If the step type has no registered executor.
        """
        executor = self._dispatch.get(config.step_type)
        if executor is None:
            raise ValueError(
                f"No executor registered for step type '{config.step_type}'. "
                f"Registered types: {list(self._dispatch.keys())}"
            )

        if config.step_type == StepType.LOAD:
            return executor(df_registry, config, recorder, file_paths or {}, file_metadata or {})
        return executor(df_registry, config, recorder)

    # ── Load ──────────────────────────────────────────────────────────────────

    def execute_load(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: LoadStepConfig,
        recorder: LineageRecorder,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
    ) -> StepExecutionResult:
        """Load a file into a DataFrame.

        Args:
            df_registry: Step name → DataFrame mapping (updated in place).
            config: Load step configuration with file_id.
            recorder: Lineage recorder.
            file_paths: Mapping of file_id → storage path.
            file_metadata: Mapping of file_id → metadata including filename.

        Returns:
            StepExecutionResult with the loaded DataFrame.

        Raises:
            FileReadError: If the file cannot be read.
            UnsupportedFileFormatError: If the file extension is not supported.
        """
        start = time.perf_counter()
        file_path = file_paths.get(config.file_id, "")
        metadata = file_metadata.get(config.file_id, {})

        df = self._read_file(config.name, file_path)
        columns = list(df.columns)
        dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

        recorder.record_load(
            file_id=config.file_id,
            file_name=metadata.get("original_filename", Path(file_path).name),
            step_name=config.name,
            columns=columns,
            dtypes=dtypes,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type=config.step_type.value if isinstance(config.step_type, StepType) else str(config.step_type),
            output_df=df,
            rows_in=len(df),
            rows_out=len(df),
            columns_in=columns,
            columns_out=columns,
            duration_ms=measure_ms(start),
        )

    def _read_file(self, step_name: str, file_path: str) -> pd.DataFrame:
        """Read a data file into a DataFrame."""
        path = Path(file_path)
        extension = path.suffix.lower()

        reader = SUPPORTED_FILE_EXTENSIONS.get(extension)
        if reader is None:
            raise UnsupportedFileFormatError(
                step_name=step_name,
                file_path=file_path,
                extension=extension,
                supported_extensions=list(SUPPORTED_FILE_EXTENSIONS.keys()),
            )

        try:
            return reader(file_path)
        except Exception as exc:
            raise FileReadError(
                step_name=step_name,
                file_path=file_path,
                reason=str(exc),
            ) from exc

    # ── Filter ────────────────────────────────────────────────────────────────

    def execute_filter(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: FilterStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Apply a row filter to the input DataFrame.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Filter step configuration.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with the filtered DataFrame.

        Raises:
            ColumnNotFoundError: If the filter column doesn't exist.
            InvalidOperatorError: If the operator is not supported.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)
        warnings: List[str] = []

        self._validate_column_exists(config.name, config.column, columns_in)
        mask = self._apply_filter_operator(config)
        filtered_df = input_df[mask(input_df[config.column], config.value)].copy()

        if len(filtered_df) == 0:
            warnings.append(
                f"Filter produced 0 rows. Condition: "
                f"{config.column} {config.operator.value} {config.value}"
            )
            logger.warning(
                "Filter produced 0 rows: step=%s, column=%s, operator=%s, value=%s",
                config.name, config.column, config.operator.value, config.value,
            )

        recorder.record_passthrough(
            step_name=config.name,
            step_type="filter",
            input_step=config.input,
            columns=list(filtered_df.columns),
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="filter",
            output_df=filtered_df,
            rows_in=len(input_df),
            rows_out=len(filtered_df),
            columns_in=columns_in,
            columns_out=list(filtered_df.columns),
            duration_ms=measure_ms(start),
            warnings=warnings,
        )

    def _apply_filter_operator(
        self, config: FilterStepConfig
    ) -> Callable[[pd.Series, Any], pd.Series]:
        """Resolve the filter operator to a callable."""
        operation = FILTER_OPERATIONS.get(config.operator)
        if operation is None:
            raise InvalidOperatorError(
                step_name=config.name,
                operator=str(config.operator),
                valid_operators=[op.value for op in FilterOperator],
            )
        return operation

    # ── Select ────────────────────────────────────────────────────────────────

    def execute_select(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SelectStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Select specific columns from the input DataFrame.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Select step configuration with column list.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with projected DataFrame.

        Raises:
            ColumnNotFoundError: If any selected column doesn't exist.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        for col in config.columns:
            self._validate_column_exists(config.name, col, columns_in)

        selected_df = input_df[config.columns].copy()
        dropped = [c for c in columns_in if c not in config.columns]

        recorder.record_projection(
            step_name=config.name,
            input_step=config.input,
            kept_columns=config.columns,
            dropped_columns=dropped,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="select",
            output_df=selected_df,
            rows_in=len(input_df),
            rows_out=len(selected_df),
            columns_in=columns_in,
            columns_out=list(selected_df.columns),
            duration_ms=measure_ms(start),
        )

    # ── Rename ────────────────────────────────────────────────────────────────

    def execute_rename(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: RenameStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Rename columns in the input DataFrame.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Rename step configuration with mapping.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with renamed columns.

        Raises:
            ColumnNotFoundError: If any source column doesn't exist.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        for old_name in config.mapping:
            self._validate_column_exists(config.name, old_name, columns_in)

        renamed_df = input_df.rename(columns=config.mapping).copy()

        recorder.record_rename(
            step_name=config.name,
            input_step=config.input,
            rename_mapping=config.mapping,
            all_columns=columns_in,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="rename",
            output_df=renamed_df,
            rows_in=len(input_df),
            rows_out=len(renamed_df),
            columns_in=columns_in,
            columns_out=list(renamed_df.columns),
            duration_ms=measure_ms(start),
        )

    # ── Join ──────────────────────────────────────────────────────────────────

    def execute_join(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: JoinStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Join two DataFrames on a specified key.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Join step configuration.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with the joined DataFrame.

        Raises:
            JoinKeyMissingError: If the join key is missing from either side.
        """
        start = time.perf_counter()
        left_df = df_registry[config.left]
        right_df = df_registry[config.right]

        self._validate_join_key(config.name, config.on, left_df, "left")
        self._validate_join_key(config.name, config.on, right_df, "right")

        joined_df = pd.merge(
            left_df, right_df,
            on=config.on,
            how=config.how.value,
            suffixes=("_left", "_right"),
        )

        recorder.record_join(
            step_name=config.name,
            left_step=config.left,
            right_step=config.right,
            left_cols=list(left_df.columns),
            right_cols=list(right_df.columns),
            output_cols=list(joined_df.columns),
            join_key=config.on,
            how=config.how.value,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="join",
            output_df=joined_df,
            rows_in=len(left_df) + len(right_df),
            rows_out=len(joined_df),
            columns_in=list(left_df.columns) + list(right_df.columns),
            columns_out=list(joined_df.columns),
            duration_ms=measure_ms(start),
        )

    def _validate_join_key(
        self,
        step_name: str,
        join_key: str,
        df: pd.DataFrame,
        side: str,
    ) -> None:
        """Validate that the join key exists in the DataFrame."""
        if join_key not in df.columns:
            raise JoinKeyMissingError(
                step_name=step_name,
                join_key=join_key,
                side=side,
                available_columns=list(df.columns),
            )

    # ── Aggregate ─────────────────────────────────────────────────────────────

    def execute_aggregate(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: AggregateStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Perform group-by aggregation on the input DataFrame.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Aggregate step configuration.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with the aggregated DataFrame.

        Raises:
            ColumnNotFoundError: If a group-by or aggregation column doesn't exist.
            AggregationError: If the aggregation operation fails.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        for col in config.group_by:
            self._validate_column_exists(config.name, col, columns_in)

        agg_dict = self._build_aggregation_dict(config, columns_in)
        aggregated_df = self._perform_aggregation(config, input_df, agg_dict)

        recorder.record_aggregate(
            step_name=config.name,
            input_step=config.input,
            group_by_cols=config.group_by,
            aggregations=config.aggregations,
            output_cols=list(aggregated_df.columns),
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="aggregate",
            output_df=aggregated_df,
            rows_in=len(input_df),
            rows_out=len(aggregated_df),
            columns_in=columns_in,
            columns_out=list(aggregated_df.columns),
            duration_ms=measure_ms(start),
        )

    def _build_aggregation_dict(
        self,
        config: AggregateStepConfig,
        available_columns: List[str],
    ) -> Dict[str, List[str]]:
        """Build the pandas aggregation dictionary from config."""
        agg_dict: Dict[str, List[str]] = {}
        for agg in config.aggregations:
            col = agg.get("column", "")
            func = agg.get("function", "")
            self._validate_column_exists(config.name, col, available_columns)
            agg_dict.setdefault(col, []).append(func)
        return agg_dict

    def _perform_aggregation(
        self,
        config: AggregateStepConfig,
        input_df: pd.DataFrame,
        agg_dict: Dict[str, List[str]],
    ) -> pd.DataFrame:
        """Execute the group-by aggregation and flatten column names."""
        try:
            grouped = input_df.groupby(config.group_by).agg(agg_dict)
        except Exception as exc:
            raise AggregationError(
                step_name=config.name,
                column=str(list(agg_dict.keys())),
                function=str(list(agg_dict.values())),
                reason=str(exc),
            ) from exc

        # Flatten multi-level column names: (column, func) → column_func
        grouped.columns = [
            f"{col}_{func}" if func != col else col
            for col, func in grouped.columns
        ]
        return grouped.reset_index()

    # ── Sort ──────────────────────────────────────────────────────────────────

    def execute_sort(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SortStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Sort the input DataFrame by a column.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Sort step configuration.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with sorted DataFrame.

        Raises:
            ColumnNotFoundError: If the sort column doesn't exist.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        self._validate_column_exists(config.name, config.by, columns_in)

        ascending = config.order == SortOrder.ASC
        sorted_df = input_df.sort_values(
            by=config.by, ascending=ascending
        ).reset_index(drop=True)

        recorder.record_passthrough(
            step_name=config.name,
            step_type="sort",
            input_step=config.input,
            columns=list(sorted_df.columns),
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="sort",
            output_df=sorted_df,
            rows_in=len(input_df),
            rows_out=len(sorted_df),
            columns_in=columns_in,
            columns_out=list(sorted_df.columns),
            duration_ms=measure_ms(start),
        )

    # ── Save ──────────────────────────────────────────────────────────────────

    def execute_save(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SaveStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Save the input DataFrame to a file.

        Args:
            df_registry: Step name → DataFrame mapping.
            config: Save step configuration with filename.
            recorder: Lineage recorder.

        Returns:
            StepExecutionResult with the unchanged DataFrame.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns = list(input_df.columns)

        recorder.record_save(
            step_name=config.name,
            input_step=config.input,
            filename=config.filename,
            columns=columns,
        )

        logger.info(
            "Save step '%s': %d rows, %d columns → %s",
            config.name, len(input_df), len(columns), config.filename,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="save",
            output_df=input_df,
            rows_in=len(input_df),
            rows_out=len(input_df),
            columns_in=columns,
            columns_out=columns,
            duration_ms=measure_ms(start),
        )

    # ── Shared Validation ─────────────────────────────────────────────────────

    def _validate_column_exists(
        self,
        step_name: str,
        column: str,
        available_columns: List[str],
    ) -> None:
        """Validate that a column exists, raising ColumnNotFoundError if not."""
        if column not in available_columns:
            raise ColumnNotFoundError(
                step_name=step_name,
                column=column,
                available_columns=available_columns,
            )
