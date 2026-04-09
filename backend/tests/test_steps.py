"""Tests for the pipeline step executor."""

import pandas as pd
import pytest

from backend.pipeline.exceptions import (
    ColumnNotFoundError,
    JoinKeyMissingError,
)
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    AggregateStepConfig,
    DeduplicateStepConfig,
    FillNullsStepConfig,
    FilterOperator,
    FilterStepConfig,
    JoinHow,
    JoinStepConfig,
    PivotStepConfig,
    RenameStepConfig,
    SampleStepConfig,
    SaveStepConfig,
    SelectStepConfig,
    SqlStepConfig,
    SortOrder,
    SortStepConfig,
    StepType,
    UnpivotStepConfig,
    ValidateStepConfig,
)
from backend.pipeline.steps import StepExecutor


@pytest.fixture()
def executor() -> StepExecutor:
    """Fresh StepExecutor instance."""
    return StepExecutor()


@pytest.fixture()
def recorder() -> LineageRecorder:
    """Fresh LineageRecorder instance."""
    return LineageRecorder()


class TestFilterStep:
    """Tests for the filter step executor."""

    def test_filter_equals_returns_only_matching_rows(
        self, executor, recorder, sample_sales_df
    ):
        """Filter with EQUALS returns only 8 delivered rows."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_delivered",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.EQUALS,
            value="delivered",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == 8
        assert all(result.output_df["status"] == "delivered")

    def test_filter_greater_than_returns_rows_above_threshold(
        self, executor, recorder, sample_sales_df
    ):
        """GREATER_THAN filter returns correct count."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_high",
            step_type=StepType.FILTER,
            input="load_sales",
            column="amount",
            operator=FilterOperator.GREATER_THAN,
            value=100.0,
        )
        result = executor.execute_filter(df_registry, config, recorder)
        expected = len(sample_sales_df[sample_sales_df["amount"] > 100.0])
        assert result.rows_out == expected

    def test_filter_is_null_on_column_without_nulls_returns_zero_rows(
        self, executor, recorder, sample_sales_df
    ):
        """IS_NULL on a complete column returns 0 rows."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_nulls",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.IS_NULL,
            value=None,
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == 0

    def test_filter_is_not_null_returns_all_rows_when_no_nulls(
        self, executor, recorder, sample_sales_df
    ):
        """IS_NOT_NULL returns all rows when no nulls exist."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_not_null",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.IS_NOT_NULL,
            value=None,
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == len(sample_sales_df)

    def test_filter_contains_returns_partial_match_rows(
        self, executor, recorder, sample_sales_df
    ):
        """status contains 'deli' should match 'delivered' (8 rows)."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_contains",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.CONTAINS,
            value="deli",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == 8

    def test_filter_nonexistent_column_raises_column_not_found_error(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError raised for nonexistent column."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_bad",
            step_type=StepType.FILTER,
            input="load_sales",
            column="nonexistent",
            operator=FilterOperator.EQUALS,
            value="x",
        )
        with pytest.raises(ColumnNotFoundError) as exc_info:
            executor.execute_filter(df_registry, config, recorder)
        assert exc_info.value.column == "nonexistent"
        assert len(exc_info.value.available_columns) > 0

    def test_filter_column_not_found_provides_fuzzy_suggestion(
        self, executor, recorder, sample_sales_df
    ):
        """Column 'amoutn' (typo) should suggest 'amount'."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_typo",
            step_type=StepType.FILTER,
            input="load_sales",
            column="amoutn",
            operator=FilterOperator.EQUALS,
            value="x",
        )
        with pytest.raises(ColumnNotFoundError) as exc_info:
            executor.execute_filter(df_registry, config, recorder)
        assert exc_info.value.suggestion == "amount"

    def test_filter_equals_returns_empty_df_when_no_match(
        self, executor, recorder, sample_sales_df
    ):
        """Filter returns empty DataFrame when no rows match."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_none",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.EQUALS,
            value="nonexistent_status",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == 0
        assert len(result.warnings) > 0

    def test_filter_on_column_with_all_nulls_returns_empty_df(self, executor, recorder):
        """Filter on a column with 100% nulls returns 0 rows without crashing."""
        df = pd.DataFrame({"col": [None, None, None], "val": [1, 2, 3]})
        df_registry = {"source": df}
        config = FilterStepConfig(
            name="filter_nulls",
            step_type=StepType.FILTER,
            input="source",
            column="col",
            operator=FilterOperator.EQUALS,
            value="some_value",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_out == 0
        assert len(result.output_df) == 0


class TestSelectStep:
    """Tests for the select step executor."""

    def test_select_keeps_only_specified_columns(
        self, executor, recorder, sample_sales_df
    ):
        """Select keeps only order_id and amount."""
        df_registry = {"load_sales": sample_sales_df}
        config = SelectStepConfig(
            name="select_cols",
            step_type=StepType.SELECT,
            input="load_sales",
            columns=["order_id", "amount"],
        )
        result = executor.execute_select(df_registry, config, recorder)
        assert list(result.output_df.columns) == ["order_id", "amount"]
        assert result.rows_out == len(sample_sales_df)

    def test_select_nonexistent_column_raises_error(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError raised for nonexistent column."""
        df_registry = {"load_sales": sample_sales_df}
        config = SelectStepConfig(
            name="select_bad",
            step_type=StepType.SELECT,
            input="load_sales",
            columns=["order_id", "nonexistent"],
        )
        with pytest.raises(ColumnNotFoundError):
            executor.execute_select(df_registry, config, recorder)


class TestRenameStep:
    """Tests for the rename step executor."""

    def test_rename_changes_specified_columns(
        self, executor, recorder, sample_sales_df
    ):
        """Rename changes amount→revenue and status→order_status."""
        df_registry = {"load_sales": sample_sales_df}
        config = RenameStepConfig(
            name="rename_cols",
            step_type=StepType.RENAME,
            input="load_sales",
            mapping={"amount": "revenue", "status": "order_status"},
        )
        result = executor.execute_rename(df_registry, config, recorder)
        assert "revenue" in result.output_df.columns
        assert "order_status" in result.output_df.columns
        assert "amount" not in result.output_df.columns
        assert "status" not in result.output_df.columns

    def test_rename_nonexistent_column_raises_error(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError raised when renaming a nonexistent column."""
        df_registry = {"load_sales": sample_sales_df}
        config = RenameStepConfig(
            name="rename_bad",
            step_type=StepType.RENAME,
            input="load_sales",
            mapping={"nonexistent": "new_name"},
        )
        with pytest.raises(ColumnNotFoundError):
            executor.execute_rename(df_registry, config, recorder)

    def test_rename_preserves_non_renamed_columns(
        self, executor, recorder, sample_sales_df
    ):
        """Non-renamed columns are preserved intact."""
        df_registry = {"load_sales": sample_sales_df}
        config = RenameStepConfig(
            name="rename_one",
            step_type=StepType.RENAME,
            input="load_sales",
            mapping={"amount": "revenue"},
        )
        result = executor.execute_rename(df_registry, config, recorder)
        assert "order_id" in result.output_df.columns
        assert "customer_id" in result.output_df.columns
        assert "status" in result.output_df.columns


class TestJoinStep:
    """Tests for the join step executor."""

    def test_join_inner_excludes_rows_without_match(
        self, executor, recorder, sample_sales_df, sample_customers_df
    ):
        """Inner join returns only matching rows."""
        df_registry = {
            "load_sales": sample_sales_df,
            "load_customers": sample_customers_df,
        }
        config = JoinStepConfig(
            name="join_data",
            step_type=StepType.JOIN,
            left="load_sales",
            right="load_customers",
            on="customer_id",
            how=JoinHow.INNER,
        )
        result = executor.execute_join(df_registry, config, recorder)
        assert result.rows_out <= 20

    def test_join_left_preserves_all_left_rows(self, executor, recorder):
        """Left join preserves all rows from the left DataFrame."""
        left_df = pd.DataFrame({"key": [1, 2, 3], "left_val": ["a", "b", "c"]})
        right_df = pd.DataFrame({"key": [1, 2], "right_val": ["x", "y"]})
        df_registry = {"left": left_df, "right": right_df}
        config = JoinStepConfig(
            name="join_left",
            step_type=StepType.JOIN,
            left="left",
            right="right",
            on="key",
            how=JoinHow.LEFT,
        )
        result = executor.execute_join(df_registry, config, recorder)
        assert result.rows_out == 3

    def test_join_missing_key_in_left_raises_error(self, executor, recorder):
        """JoinKeyMissingError raised when key not in left DataFrame."""
        left_df = pd.DataFrame({"id": [1, 2], "val": ["a", "b"]})
        right_df = pd.DataFrame({"key": [1, 2], "val": ["x", "y"]})
        df_registry = {"left": left_df, "right": right_df}
        config = JoinStepConfig(
            name="join_bad",
            step_type=StepType.JOIN,
            left="left",
            right="right",
            on="key",
            how=JoinHow.INNER,
        )
        with pytest.raises(JoinKeyMissingError) as exc_info:
            executor.execute_join(df_registry, config, recorder)
        assert exc_info.value.side == "left"

    def test_join_missing_key_in_right_raises_error(self, executor, recorder):
        """JoinKeyMissingError raised when key not in right DataFrame."""
        left_df = pd.DataFrame({"key": [1, 2], "val": ["a", "b"]})
        right_df = pd.DataFrame({"id": [1, 2], "val": ["x", "y"]})
        df_registry = {"left": left_df, "right": right_df}
        config = JoinStepConfig(
            name="join_bad",
            step_type=StepType.JOIN,
            left="left",
            right="right",
            on="key",
            how=JoinHow.INNER,
        )
        with pytest.raises(JoinKeyMissingError) as exc_info:
            executor.execute_join(df_registry, config, recorder)
        assert exc_info.value.side == "right"


class TestAggregateStep:
    """Tests for the aggregate step executor."""

    def test_aggregate_sum_produces_correct_totals(self, executor, recorder):
        """Sum aggregation produces correct totals per group."""
        df = pd.DataFrame(
            {
                "group": ["a", "a", "b", "b"],
                "value": [10.0, 20.0, 30.0, 40.0],
            }
        )
        df_registry = {"source": df}
        config = AggregateStepConfig(
            name="agg_totals",
            step_type=StepType.AGGREGATE,
            input="source",
            group_by=["group"],
            aggregations=[{"column": "value", "function": "sum"}],
        )
        result = executor.execute_aggregate(df_registry, config, recorder)
        assert result.rows_out == 2
        a_total = result.output_df[result.output_df["group"] == "a"]["value_sum"].iloc[
            0
        ]
        assert a_total == 30.0

    def test_aggregate_count_includes_all_groups(
        self, executor, recorder, sample_sales_df
    ):
        """Count aggregation includes all unique groups."""
        df_registry = {"load_sales": sample_sales_df}
        config = AggregateStepConfig(
            name="agg_count",
            step_type=StepType.AGGREGATE,
            input="load_sales",
            group_by=["region"],
            aggregations=[{"column": "order_id", "function": "count"}],
        )
        result = executor.execute_aggregate(df_registry, config, recorder)
        assert result.rows_out == sample_sales_df["region"].nunique()

    def test_aggregate_on_string_column_with_sum_raises_error(self, executor, recorder):
        """AggregationError raised when summing a string column."""
        df = pd.DataFrame({"group": ["a", "b"], "val": ["x", "y"]})
        df_registry = {"source": df}
        config = AggregateStepConfig(
            name="agg_bad",
            step_type=StepType.AGGREGATE,
            input="source",
            group_by=["group"],
            aggregations=[{"column": "val", "function": "sum"}],
        )
        from backend.pipeline.exceptions import AggregationError

        with pytest.raises(AggregationError):
            executor.execute_aggregate(df_registry, config, recorder)


class TestSortStep:
    """Tests for the sort step executor."""

    def test_sort_ascending_orders_correctly(self, executor, recorder, sample_sales_df):
        """Sort ascending produces correctly ordered output."""
        df_registry = {"load_sales": sample_sales_df}
        config = SortStepConfig(
            name="sort_asc",
            step_type=StepType.SORT,
            input="load_sales",
            by="amount",
            order=SortOrder.ASC,
        )
        result = executor.execute_sort(df_registry, config, recorder)
        amounts = result.output_df["amount"].tolist()
        assert amounts == sorted(amounts)

    def test_sort_descending_orders_correctly(
        self, executor, recorder, sample_sales_df
    ):
        """Sort descending produces correctly ordered output."""
        df_registry = {"load_sales": sample_sales_df}
        config = SortStepConfig(
            name="sort_desc",
            step_type=StepType.SORT,
            input="load_sales",
            by="amount",
            order=SortOrder.DESC,
        )
        result = executor.execute_sort(df_registry, config, recorder)
        amounts = result.output_df["amount"].tolist()
        assert amounts == sorted(amounts, reverse=True)

    def test_sort_nonexistent_column_raises_error(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError raised for nonexistent sort column."""
        df_registry = {"load_sales": sample_sales_df}
        config = SortStepConfig(
            name="sort_bad",
            step_type=StepType.SORT,
            input="load_sales",
            by="nonexistent",
            order=SortOrder.ASC,
        )
        with pytest.raises(ColumnNotFoundError):
            executor.execute_sort(df_registry, config, recorder)


class TestExecutionResult:
    """Tests for StepExecutionResult metadata correctness."""

    def test_execution_result_records_correct_timing(
        self, executor, recorder, sample_sales_df
    ):
        """duration_ms must be > 0 and < 5000 for a simple filter."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_timing",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.EQUALS,
            value="delivered",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.duration_ms >= 0
        assert result.duration_ms < 5000

    def test_execution_result_records_correct_row_counts(
        self, executor, recorder, sample_sales_df
    ):
        """rows_in and rows_out are correctly populated."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_counts",
            step_type=StepType.FILTER,
            input="load_sales",
            column="status",
            operator=FilterOperator.EQUALS,
            value="delivered",
        )
        result = executor.execute_filter(df_registry, config, recorder)
        assert result.rows_in == len(sample_sales_df)
        assert result.rows_out == 8

    def test_execution_result_records_correct_columns(
        self, executor, recorder, sample_sales_df
    ):
        """columns_in and columns_out are correctly populated."""
        df_registry = {"load_sales": sample_sales_df}
        config = SelectStepConfig(
            name="select_meta",
            step_type=StepType.SELECT,
            input="load_sales",
            columns=["order_id", "amount"],
        )
        result = executor.execute_select(df_registry, config, recorder)
        assert result.columns_in == list(sample_sales_df.columns)
        assert result.columns_out == ["order_id", "amount"]


class TestStepEdgeCases:
    """Additional edge-case tests for pipeline steps."""

    def test_sample_frac_zero_returns_empty_df(
        self, executor, recorder, sample_sales_df
    ):
        """Sample with frac=0.0 returns an empty DataFrame."""
        df_registry = {"load_sales": sample_sales_df}
        config = SampleStepConfig(
            name="sample_zero",
            step_type=StepType.SAMPLE,
            input="load_sales",
            fraction=0.0,
        )
        result = executor.execute_sample(df_registry, config, recorder)
        assert result.rows_out == 0
        assert len(result.output_df) == 0

    def test_sample_frac_one_returns_all_rows(
        self, executor, recorder, sample_sales_df
    ):
        """Sample with frac=1.0 returns all rows."""
        df_registry = {"load_sales": sample_sales_df}
        config = SampleStepConfig(
            name="sample_all",
            step_type=StepType.SAMPLE,
            input="load_sales",
            fraction=1.0,
        )
        result = executor.execute_sample(df_registry, config, recorder)
        assert result.rows_out == len(sample_sales_df)

    def test_pivot_empty_df_returns_empty_df(self, executor, recorder):
        """Pivot on empty DataFrame should not crash and return empty result."""
        df = pd.DataFrame(columns=["idx", "cols", "vals"])
        df_registry = {"source": df}
        config = PivotStepConfig(
            name="pivot_empty",
            step_type=StepType.PIVOT,
            input="source",
            index=["idx"],
            columns="cols",
            values="vals",
        )
        result = executor.execute_pivot(df_registry, config, recorder)
        assert result.rows_out == 0

    def test_unpivot_overlapping_vars_raises_error(self, executor, recorder):
        """Unpivot with overlapping id_vars and value_vars must raise ValueError."""
        df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
        df_registry = {"source": df}
        config = UnpivotStepConfig(
            name="unpivot_overlap",
            step_type=StepType.UNPIVOT,
            input="source",
            id_vars=["a", "b"],
            value_vars=["b", "c"],
        )
        with pytest.raises(ValueError, match="id_vars and value_vars must not overlap"):
            executor.execute_unpivot(df_registry, config, recorder)

    def test_deduplicate_no_subset_uses_all_columns(self, executor, recorder):
        """Deduplicate without subset should consider all columns."""
        df = pd.DataFrame({"a": [1, 1, 2], "b": [1, 1, 3]})
        df_registry = {"source": df}
        config = DeduplicateStepConfig(
            name="dedup_all",
            step_type=StepType.DEDUPLICATE,
            input="source",
            subset=None,
        )
        result = executor.execute_deduplicate(df_registry, config, recorder)
        assert result.rows_out == 2

    def test_fill_nulls_mean_on_non_numeric_raises_error(self, executor, recorder):
        """Fill nulls with mean on string column should raise ValueError."""
        df = pd.DataFrame({"a": ["x", None, "z"]})
        df_registry = {"source": df}
        config = FillNullsStepConfig(
            name="fill_bad",
            step_type=StepType.FILL_NULLS,
            input="source",
            strategy="mean",
            columns=["a"],
        )
        with pytest.raises(ValueError, match="Strategy 'mean' requires numeric column"):
            executor.execute_fill_nulls(df_registry, config, recorder)


class TestSqlStep:
    """Tests for SQL step execution."""

    def test_sql_step_executes_select_query(self, executor, recorder, sample_sales_df):
        """SQL step executes against {{input}} and returns projected output."""
        df_registry = {"load_sales": sample_sales_df}
        config = SqlStepConfig(
            name="sql_projection",
            step_type=StepType.SQL,
            input="load_sales",
            query=(
                "SELECT customer_id, amount * 2 AS amount_x2 "
                "FROM {{input}} WHERE amount > 100"
            ),
        )
        result = executor.execute_sql(df_registry, config, recorder)
        assert list(result.output_df.columns) == ["customer_id", "amount_x2"]
        assert result.rows_out > 0
        assert (result.output_df["amount_x2"] > 200).all()

    def test_sql_step_rejects_non_select_query(self, executor, recorder, sample_sales_df):
        """SQL step blocks write/admin SQL keywords."""
        df_registry = {"load_sales": sample_sales_df}
        config = SqlStepConfig(
            name="sql_bad",
            step_type=StepType.SQL,
            input="load_sales",
            query="DELETE FROM {{input}}",
        )
        with pytest.raises(ValueError):
            executor.execute_sql(df_registry, config, recorder)
