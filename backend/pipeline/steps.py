"""Pipeline step executor with dispatch-dict pattern.

Implements one public method per step type, dispatched via a clean
mapping instead of if-elif chains. Each method follows the same
signature pattern, returning a StepExecutionResult with timing,
row counts, and column metadata.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import pandas as pd
from backend.execution.smart_executor import SmartExecutor
from backend.services.storage_service import storage_service
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
    DeduplicateStepConfig,
    FillNullsStepConfig,
    FilterOperator,
    FilterStepConfig,
    JoinStepConfig,
    LoadStepConfig,
    PivotStepConfig,
    RenameStepConfig,
    SampleStepConfig,
    SaveStepConfig,
    SelectStepConfig,
    SqlStepConfig,
    SortOrder,
    SortStepConfig,
    StepConfig,
    StepType,
    UnpivotStepConfig,
    ValidateStepConfig,
)
from backend.utils.time_utils import measure_ms

logger = logging.getLogger(__name__)


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


# Maps FilterOperator → a callable that takes (pd.Series, value) → pd.Series[bool]
FILTER_OPERATIONS: Dict[FilterOperator, Callable[[pd.Series, Any], pd.Series]] = {
    FilterOperator.EQUALS: lambda s, v: s == v,
    FilterOperator.NOT_EQUALS: lambda s, v: s != v,
    FilterOperator.GREATER_THAN: lambda s, v: s > v,
    FilterOperator.LESS_THAN: lambda s, v: s < v,
    FilterOperator.GREATER_THAN_OR_EQUAL: lambda s, v: s >= v,
    FilterOperator.LESS_THAN_OR_EQUAL: lambda s, v: s <= v,
    FilterOperator.CONTAINS: lambda s, v: s.astype(str).str.contains(str(v), na=False),
    FilterOperator.NOT_CONTAINS: lambda s, v: (
        ~s.astype(str).str.contains(str(v), na=False)
    ),
    FilterOperator.STARTS_WITH: lambda s, v: s.astype(str).str.startswith(
        str(v), na=False
    ),
    FilterOperator.ENDS_WITH: lambda s, v: s.astype(str).str.endswith(str(v), na=False),
    FilterOperator.IS_NULL: lambda s, _: s.isna(),
    FilterOperator.IS_NOT_NULL: lambda s, _: s.notna(),
}

SUPPORTED_FILE_EXTENSIONS: Dict[str, Callable[..., pd.DataFrame]] = {
    ".csv": pd.read_csv,
    ".json": pd.read_json,
}


class StepExecutor:
    """Executes individual pipeline steps and records lineage.

    Uses a dispatch dictionary to map StepType enum values to executor
    methods, avoiding long if-elif chains. Each executor method follows
    the same pattern: validate inputs, execute transformation, record
    lineage, and return StepExecutionResult.
    """

    def __init__(self) -> None:
        self._smart_executor = SmartExecutor()
        self._dispatch: Dict[StepType, Callable] = {
            StepType.LOAD: self.execute_load,
            StepType.FILTER: self.execute_filter,
            StepType.SELECT: self.execute_select,
            StepType.RENAME: self.execute_rename,
            StepType.JOIN: self.execute_join,
            StepType.AGGREGATE: self.execute_aggregate,
            StepType.SORT: self.execute_sort,
            StepType.SAVE: self.execute_save,
            StepType.VALIDATE: self.execute_validate,
            StepType.PIVOT: self.execute_pivot,
            StepType.UNPIVOT: self.execute_unpivot,
            StepType.DEDUPLICATE: self.execute_deduplicate,
            StepType.FILL_NULLS: self.execute_fill_nulls,
            StepType.SAMPLE: self.execute_sample,
            StepType.SQL: self.execute_sql,
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
            return executor(
                df_registry, config, recorder, file_paths or {}, file_metadata or {}
            )
        return executor(df_registry, config, recorder)

    def execute_load(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: LoadStepConfig,
        recorder: LineageRecorder,
        file_paths: Dict[str, str],
        file_metadata: Dict[str, Dict[str, str]],
    ) -> StepExecutionResult:
        """Load a file into a DataFrame.

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
            step_type=config.step_type.value
            if isinstance(config.step_type, StepType)
            else str(config.step_type),
            output_df=df,
            rows_in=len(df),
            rows_out=len(df),
            columns_in=columns,
            columns_out=columns,
            duration_ms=measure_ms(start),
        )

    def _read_file(self, step_name: str, file_path: str) -> pd.DataFrame:
        """Read a data file into a DataFrame."""
        extension = Path(file_path).suffix.lower()
        reader = SUPPORTED_FILE_EXTENSIONS.get(extension)
        if reader is None:
            raise UnsupportedFileFormatError(
                step_name=step_name,
                file_path=file_path,
                extension=extension,
                supported_extensions=list(SUPPORTED_FILE_EXTENSIONS.keys()),
            )
        try:
            with storage_service.download(file_path) as handle:
                return reader(handle)
        except Exception as exc:
            raise FileReadError(
                step_name=step_name,
                file_path=file_path,
                reason=str(exc),
            ) from exc

    def execute_filter(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: FilterStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Apply a row filter to the input DataFrame.

        Raises:
            ColumnNotFoundError: If the filter column doesn't exist.
            InvalidOperatorError: If the operator is not supported.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)
        warnings: List[str] = []

        self._validate_column_exists(config.name, config.column, columns_in)

        def _pandas_filter() -> pd.DataFrame:
            mask = self._apply_filter_operator(config)
            return input_df[mask(input_df[config.column], config.value)].copy()

        filtered_df = self._smart_executor.execute_unary(config, input_df, _pandas_filter)

        if len(filtered_df) == 0:
            warnings.append(
                f"Filter produced 0 rows. Condition: "
                f"{config.column} {config.operator.value} {config.value}"
            )
            logger.warning(
                "Filter produced 0 rows: step=%s, column=%s, operator=%s, value=%s",
                config.name,
                config.column,
                config.operator.value,
                config.value,
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

    def execute_select(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SelectStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Select specific columns from the input DataFrame.

        Raises:
            ColumnNotFoundError: If any selected column doesn't exist.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        for col in config.columns:
            self._validate_column_exists(config.name, col, columns_in)

        def _pandas_select() -> pd.DataFrame:
            return input_df[config.columns].copy()

        selected_df = self._smart_executor.execute_unary(config, input_df, _pandas_select)
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

    # Rename

    def execute_rename(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: RenameStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Rename columns in the input DataFrame.

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

    def execute_join(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: JoinStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Join two DataFrames on a specified key.

        Raises:
            JoinKeyMissingError: If the join key is missing from either side.
        """
        start = time.perf_counter()
        left_df = df_registry[config.left]
        right_df = df_registry[config.right]

        self._validate_join_key(config.name, config.on, left_df, "left")
        self._validate_join_key(config.name, config.on, right_df, "right")

        def _pandas_join() -> pd.DataFrame:
            return pd.merge(
                left_df,
                right_df,
                on=config.on,
                how=config.how.value,
                suffixes=("_left", "_right"),
            )

        joined_df = self._smart_executor.execute_join(
            config,
            left_df,
            right_df,
            _pandas_join,
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

    def execute_aggregate(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: AggregateStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Perform group-by aggregation on the input DataFrame.

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

        def _pandas_aggregate() -> pd.DataFrame:
            return self._perform_aggregation(config, input_df, agg_dict)

        aggregated_df = self._smart_executor.execute_unary(
            config,
            input_df,
            _pandas_aggregate,
        )

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
        # Ensure numeric columns for numeric aggregations
        numeric_funcs = {"sum", "mean", "median", "std", "var"}
        for col, funcs in agg_dict.items():
            for func in funcs:
                if func in numeric_funcs and not pd.api.types.is_numeric_dtype(
                    input_df[col]
                ):
                    raise AggregationError(
                        step_name=config.name,
                        column=col,
                        function=func,
                        reason=f"Aggregation function '{func}' requires numeric column, but '{col}' is {input_df[col].dtype}",
                    )

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
            f"{col}_{func}" if func != col else col for col, func in grouped.columns
        ]
        return grouped.reset_index()

    def execute_sort(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SortStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Sort the input DataFrame by a column.

        Raises:
            ColumnNotFoundError: If the sort column doesn't exist.
        """
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        self._validate_column_exists(config.name, config.by, columns_in)

        def _pandas_sort() -> pd.DataFrame:
            ascending = config.order == SortOrder.ASC
            return input_df.sort_values(by=config.by, ascending=ascending).reset_index(
                drop=True
            )

        sorted_df = self._smart_executor.execute_unary(config, input_df, _pandas_sort)

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

    def execute_save(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SaveStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Save the input DataFrame to a file on disk (CSV or JSON)."""
        import uuid as _uuid
        import io

        from backend.config import settings

        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns = list(input_df.columns)

        # Determine extension from filename or default to .csv
        filename = config.filename
        ext = Path(filename).suffix.lower() or ".csv"
        if ext not in [".csv", ".json"]:
            ext = ".csv"

        stored_path = f"{filename}_{_uuid.uuid4().hex}{ext}"

        try:
            if ext == ".json":
                buffer = io.BytesIO()
                input_df.to_json(buffer, orient="records", indent=2)
                buffer.seek(0)
            else:
                buffer = io.BytesIO()
                input_df.to_csv(buffer, index=False)
                buffer.seek(0)

            storage_service.upload(buffer, stored_path)
        except Exception as exc:
            logger.error("Failed to save file %s: %s", stored_path, exc)
            raise FileReadError(  # Reusing FileReadError as a generic storage error
                step_name=config.name,
                file_path=stored_path,
                reason=str(exc),
            ) from exc

        recorder.record_save(
            step_name=config.name,
            input_step=config.input,
            filename=config.filename,
            columns=columns,
        )

        logger.info(
            "Save step '%s': %d rows, %d columns → %s",
            config.name,
            len(input_df),
            len(columns),
            stored_path,
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

    def execute_validate(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: ValidateStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Run data quality validation rules against the input DataFrame."""
        from backend.pipeline.validators import execute_validate as run_validate

        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns = list(input_df.columns)
        warnings: List[str] = []

        result = run_validate(input_df, config.rules, config.name)

        if not result.passed:
            warnings.append(
                f"Validation failed: {result.error_count} errors, "
                f"{result.warning_count} warnings"
            )
        elif result.warning_count > 0:
            warnings.append(f"Validation passed with {result.warning_count} warnings")

        recorder.record_passthrough(
            step_name=config.name,
            step_type="validate",
            input_step=config.input,
            columns=columns,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="validate",
            output_df=result.output_df,
            rows_in=len(input_df),
            rows_out=len(result.output_df),
            columns_in=columns,
            columns_out=columns,
            duration_ms=measure_ms(start),
            warnings=warnings,
        )

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

    def execute_pivot(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: PivotStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Reshape data from long to wide format."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        all_cols = config.index + [config.columns, config.values]
        for col in all_cols:
            self._validate_column_exists(config.name, col, columns_in)

        def _pandas_pivot() -> pd.DataFrame:
            result_df = input_df.pivot_table(
                index=config.index,
                columns=config.columns,
                values=config.values,
                aggfunc=config.aggfunc,
                fill_value=config.fill_value,
            )
            if isinstance(result_df.columns, pd.MultiIndex):
                result_df.columns = [
                    f"{col[1]}_{col[0]}" if isinstance(col, tuple) else str(col)
                    for col in result_df.columns
                ]
            else:
                result_df.columns = [str(col) for col in result_df.columns]
            return result_df.reset_index()

        result = self._smart_executor.execute_unary(config, input_df, _pandas_pivot)

        output_columns = list(result.columns)
        recorder.record_pivot(
            step_name=config.name,
            input_step=config.input,
            index_col=config.index[0] if config.index else "",
            columns_col=config.columns,
            values_col=config.values,
            output_columns=output_columns,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="pivot",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )

    def execute_unpivot(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: UnpivotStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Reshape data from wide to long format."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        all_cols = config.id_vars + config.value_vars
        for col in all_cols:
            self._validate_column_exists(config.name, col, columns_in)

        overlap = set(config.id_vars) & set(config.value_vars)
        if overlap:
            raise ValueError(f"id_vars and value_vars must not overlap: {overlap}")

        def _pandas_unpivot() -> pd.DataFrame:
            return input_df.melt(
                id_vars=config.id_vars,
                value_vars=config.value_vars,
                var_name=config.var_name,
                value_name=config.value_name,
            )

        result = self._smart_executor.execute_unary(config, input_df, _pandas_unpivot)

        output_columns = list(result.columns)
        recorder.record_unpivot(
            step_name=config.name,
            input_step=config.input,
            id_columns=config.id_vars,
            value_columns=config.value_vars,
            output_columns=output_columns,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="unpivot",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )

    def execute_deduplicate(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: DeduplicateStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Remove duplicate rows."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        if config.subset:
            for col in config.subset:
                self._validate_column_exists(config.name, col, columns_in)

        def _pandas_deduplicate() -> pd.DataFrame:
            keep = False if config.keep == "none" else config.keep
            deduped = input_df.drop_duplicates(subset=config.subset, keep=keep)
            return deduped.reset_index(drop=True)

        result = self._smart_executor.execute_unary(
            config,
            input_df,
            _pandas_deduplicate,
        )

        recorder.record_deduplicate(
            step_name=config.name,
            input_step=config.input,
            columns=list(result.columns),
            subset=config.subset,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="deduplicate",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )

    def execute_fill_nulls(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: FillNullsStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Fill missing values."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        for col in config.columns:
            self._validate_column_exists(config.name, col, columns_in)

        def _pandas_fill_nulls() -> pd.DataFrame:
            result_df = input_df.copy()
            for col in config.columns:
                if config.strategy == "constant":
                    if config.constant_value is None:
                        raise ValueError(
                            "constant_value required when strategy is 'constant'"
                        )
                    result_df[col] = result_df[col].fillna(config.constant_value)
                elif config.strategy == "forward_fill":
                    result_df[col] = result_df[col].ffill()
                elif config.strategy == "backward_fill":
                    result_df[col] = result_df[col].bfill()
                elif config.strategy == "mean":
                    if not pd.api.types.is_numeric_dtype(result_df[col]):
                        raise ValueError("Strategy 'mean' requires numeric column")
                    result_df[col] = result_df[col].fillna(result_df[col].mean())
                elif config.strategy == "median":
                    if not pd.api.types.is_numeric_dtype(result_df[col]):
                        raise ValueError("Strategy 'median' requires numeric column")
                    result_df[col] = result_df[col].fillna(result_df[col].median())
                elif config.strategy == "mode":
                    mode_val = result_df[col].mode()
                    if len(mode_val) > 0:
                        result_df[col] = result_df[col].fillna(mode_val[0])
            return result_df

        result = self._smart_executor.execute_unary(config, input_df, _pandas_fill_nulls)

        recorder.record_fill_nulls(
            step_name=config.name,
            input_step=config.input,
            columns=list(result.columns),
            method=config.strategy,
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="fill_nulls",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )

    def execute_sample(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SampleStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Take a random sample of rows."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)

        if config.n is None and config.fraction is None:
            raise ValueError("Either n or fraction must be specified")
        if config.n is not None and config.fraction is not None:
            raise ValueError("Specify either n or fraction, not both")

        if config.stratify_by:
            self._validate_column_exists(config.name, config.stratify_by, columns_in)

        def _pandas_sample() -> pd.DataFrame:
            if config.n is not None and config.n > len(input_df):
                sampled_df = input_df.reset_index(drop=True)
            elif config.stratify_by:
                groups = []
                target_n = config.n or int(len(input_df) * config.fraction)
                for _, group_df in input_df.groupby(config.stratify_by):
                    group_n = max(1, int(len(group_df) / len(input_df) * target_n))
                    groups.append(
                        group_df.sample(
                            n=min(group_n, len(group_df)),
                            random_state=config.random_state,
                        )
                    )
                sampled_df = pd.concat(groups).sample(
                    frac=1,
                    random_state=config.random_state,
                )
            elif config.n is not None:
                sampled_df = input_df.sample(n=config.n, random_state=config.random_state)
            else:
                sampled_df = input_df.sample(
                    frac=config.fraction,
                    random_state=config.random_state,
                )
            return sampled_df.reset_index(drop=True)

        result = self._smart_executor.execute_unary(config, input_df, _pandas_sample)

        recorder.record_sample(
            step_name=config.name,
            input_step=config.input,
            columns=list(result.columns),
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="sample",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )

    def execute_sql(
        self,
        df_registry: Dict[str, pd.DataFrame],
        config: SqlStepConfig,
        recorder: LineageRecorder,
    ) -> StepExecutionResult:
        """Execute a SQL step using DuckDB against the input DataFrame."""
        start = time.perf_counter()
        input_df = df_registry[config.input]
        columns_in = list(input_df.columns)
        result = self._smart_executor.execute_sql(config, input_df)

        recorder.record_sql(
            step_name=config.name,
            input_step=config.input,
            input_columns=columns_in,
            output_columns=list(result.columns),
        )

        return StepExecutionResult(
            step_name=config.name,
            step_type="sql",
            output_df=result,
            rows_in=len(input_df),
            rows_out=len(result),
            columns_in=columns_in,
            columns_out=list(result.columns),
            duration_ms=measure_ms(start),
        )
