"""Tests for the pipeline step executor."""

# Third-party packages
import pandas as pd
import pytest

# Internal modules
from backend.pipeline.exceptions import (
    ColumnNotFoundError,
    JoinKeyMissingError,
)
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import (
    AggregateStepConfig,
    FilterOperator,
    FilterStepConfig,
    JoinHow,
    JoinStepConfig,
    RenameStepConfig,
    SelectStepConfig,
    SortOrder,
    SortStepConfig,
    StepType,
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


# ═══════════════════════════════════════════════════════════════════════════════
# FILTER STEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilterStep:
    """Tests for the filter step executor."""

    def test_filter_equals_returns_matching_rows(
        self, executor, recorder, sample_sales_df
    ):
        """Filter with EQUALS operator returns only matching rows."""
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

        assert result.rows_out > 0
        assert all(result.output_df["status"] == "delivered")

    def test_filter_equals_returns_empty_df_when_no_match(
        self, executor, recorder, sample_sales_df
    ):
        """Filter returns empty DataFrame when no rows match (not an error)."""
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

    def test_filter_raises_column_not_found_error(
        self, executor, recorder, sample_sales_df
    ):
        """Filter raises ColumnNotFoundError for nonexistent column."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_bad",
            step_type=StepType.FILTER,
            input="load_sales",
            column="nonexistent_column",
            operator=FilterOperator.EQUALS,
            value="test",
        )

        with pytest.raises(ColumnNotFoundError) as exc_info:
            executor.execute_filter(df_registry, config, recorder)

        assert "nonexistent_column" in str(exc_info.value)

    def test_filter_column_not_found_suggests_closest_match(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError includes a fuzzy match suggestion."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_typo",
            step_type=StepType.FILTER,
            input="load_sales",
            column="statu",  # typo for "status"
            operator=FilterOperator.EQUALS,
            value="delivered",
        )

        with pytest.raises(ColumnNotFoundError) as exc_info:
            executor.execute_filter(df_registry, config, recorder)

        assert exc_info.value.suggestion == "status"

    def test_filter_is_null_works_on_column_with_nulls(
        self, executor, recorder
    ):
        """IS_NULL filter correctly identifies null values."""
        df = pd.DataFrame({
            "name": ["Alice", None, "Charlie", None, "Eve"],
            "value": [1, 2, 3, 4, 5],
        })
        df_registry = {"source": df}
        config = FilterStepConfig(
            name="filter_nulls",
            step_type=StepType.FILTER,
            input="source",
            column="name",
            operator=FilterOperator.IS_NULL,
            value=None,
        )

        result = executor.execute_filter(df_registry, config, recorder)

        assert result.rows_out == 2

    def test_filter_greater_than_works_on_numeric_column(
        self, executor, recorder, sample_sales_df
    ):
        """GREATER_THAN filter works correctly on numeric columns."""
        df_registry = {"load_sales": sample_sales_df}
        config = FilterStepConfig(
            name="filter_high_amount",
            step_type=StepType.FILTER,
            input="load_sales",
            column="amount",
            operator=FilterOperator.GREATER_THAN,
            value=300,
        )

        result = executor.execute_filter(df_registry, config, recorder)

        assert all(result.output_df["amount"] > 300)


# ═══════════════════════════════════════════════════════════════════════════════
# JOIN STEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestJoinStep:
    """Tests for the join step executor."""

    def test_join_inner_excludes_non_matching_rows(
        self, executor, recorder, sample_sales_df, sample_customers_df
    ):
        """Inner join only includes rows with matching keys in both sides."""
        df_registry = {
            "load_sales": sample_sales_df,
            "load_customers": sample_customers_df,
        }
        config = JoinStepConfig(
            name="join_sales_customers",
            step_type=StepType.JOIN,
            left="load_sales",
            right="load_customers",
            on="customer_id",
            how=JoinHow.INNER,
        )

        result = executor.execute_join(df_registry, config, recorder)

        assert result.rows_out > 0
        assert "customer_id" in result.output_df.columns

    def test_join_left_preserves_all_left_rows(
        self, executor, recorder
    ):
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

        assert result.rows_out == 3  # all left rows preserved

    def test_join_raises_error_when_key_missing_from_left(
        self, executor, recorder
    ):
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

    def test_join_raises_error_when_key_missing_from_right(
        self, executor, recorder
    ):
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


# ═══════════════════════════════════════════════════════════════════════════════
# AGGREGATE STEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestAggregateStep:
    """Tests for the aggregate step executor."""

    def test_aggregate_sum_produces_correct_totals(
        self, executor, recorder, sample_sales_df
    ):
        """Sum aggregation produces correct totals per group."""
        df_registry = {"load_sales": sample_sales_df}
        config = AggregateStepConfig(
            name="agg_totals",
            step_type=StepType.AGGREGATE,
            input="load_sales",
            group_by=["customer_id"],
            aggregations=[
                {"column": "amount", "function": "sum"},
            ],
        )

        result = executor.execute_aggregate(df_registry, config, recorder)

        assert "customer_id" in result.output_df.columns
        assert "amount_sum" in result.output_df.columns
        assert result.rows_out > 0

    def test_aggregate_count_includes_all_groups(
        self, executor, recorder, sample_sales_df
    ):
        """Count aggregation includes all unique groups."""
        df_registry = {"load_sales": sample_sales_df}
        config = AggregateStepConfig(
            name="agg_count",
            step_type=StepType.AGGREGATE,
            input="load_sales",
            group_by=["status"],
            aggregations=[
                {"column": "order_id", "function": "count"},
            ],
        )

        result = executor.execute_aggregate(df_registry, config, recorder)

        unique_statuses = sample_sales_df["status"].nunique()
        assert result.rows_out == unique_statuses


# ═══════════════════════════════════════════════════════════════════════════════
# RENAME STEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestRenameStep:
    """Tests for the rename step executor."""

    def test_rename_changes_column_names(
        self, executor, recorder, sample_sales_df
    ):
        """Rename step changes specified column names."""
        df_registry = {"load_sales": sample_sales_df}
        config = RenameStepConfig(
            name="rename_cols",
            step_type=StepType.RENAME,
            input="load_sales",
            mapping={"amount": "total_amount", "status": "order_status"},
        )

        result = executor.execute_rename(df_registry, config, recorder)

        assert "total_amount" in result.columns_out
        assert "order_status" in result.columns_out
        assert "amount" not in result.columns_out

    def test_rename_raises_error_for_nonexistent_column(
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


# ═══════════════════════════════════════════════════════════════════════════════
# SELECT STEP TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSelectStep:
    """Tests for the select step executor."""

    def test_select_keeps_only_specified_columns(
        self, executor, recorder, sample_sales_df
    ):
        """Select step keeps only the specified columns."""
        df_registry = {"load_sales": sample_sales_df}
        config = SelectStepConfig(
            name="select_cols",
            step_type=StepType.SELECT,
            input="load_sales",
            columns=["order_id", "amount", "status"],
        )

        result = executor.execute_select(df_registry, config, recorder)

        assert result.columns_out == ["order_id", "amount", "status"]
        assert len(result.columns_out) == 3

    def test_select_raises_error_for_nonexistent_column(
        self, executor, recorder, sample_sales_df
    ):
        """ColumnNotFoundError raised when selecting a nonexistent column."""
        df_registry = {"load_sales": sample_sales_df}
        config = SelectStepConfig(
            name="select_bad",
            step_type=StepType.SELECT,
            input="load_sales",
            columns=["order_id", "nonexistent_col"],
        )

        with pytest.raises(ColumnNotFoundError):
            executor.execute_select(df_registry, config, recorder)
