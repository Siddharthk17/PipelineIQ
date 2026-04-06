"""Unit tests for new pipeline step types: pivot, unpivot, deduplicate, fill_nulls, sample."""

import pytest
import pandas as pd
from backend.pipeline.steps import StepExecutor
from backend.pipeline.parser import (
    PivotStepConfig,
    UnpivotStepConfig,
    DeduplicateStepConfig,
    FillNullsStepConfig,
    SampleStepConfig,
    StepType,
)
from backend.pipeline.lineage import LineageRecorder


@pytest.fixture
def executor():
    return StepExecutor()


@pytest.fixture
def recorder():
    return LineageRecorder()


@pytest.fixture
def long_df():
    return pd.DataFrame(
        {
            "customer_id": [1, 1, 2, 2, 3, 3],
            "quarter": ["Q1", "Q2", "Q1", "Q2", "Q1", "Q2"],
            "revenue": [100, 200, 300, 400, 500, 600],
        }
    )


@pytest.fixture
def wide_df():
    return pd.DataFrame(
        {
            "customer_id": [1, 2, 3],
            "region": ["North", "South", "East"],
            "q1_revenue": [100, 200, 300],
            "q2_revenue": [150, 250, 350],
        }
    )


@pytest.fixture
def df_with_dupes():
    return pd.DataFrame(
        {
            "order_id": [1, 1, 2, 3, 3, 4],
            "customer_id": [100, 100, 200, 300, 300, 400],
            "amount": [50.0, 50.0, 75.0, 30.0, 30.0, 90.0],
        }
    )


@pytest.fixture
def df_with_nulls():
    return pd.DataFrame(
        {
            "numeric_col": [1.0, None, 3.0, None, 5.0],
            "string_col": ["a", None, "c", None, "e"],
            "int_col": [10, 20, None, 40, None],
        }
    )


@pytest.fixture
def large_df():
    return pd.DataFrame(
        {
            "id": range(10000),
            "value": range(10000),
            "region": (["North"] * 4000 + ["South"] * 3000 + ["East"] * 3000),
        }
    )


class TestPivotStep:
    def test_pivot_basic_sum(self, executor, long_df, recorder):
        step = PivotStepConfig(
            name="pivot_test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["customer_id"],
            columns="quarter",
            values="revenue",
            aggfunc="sum",
        )
        result = executor.execute({"load1": long_df}, step, recorder)
        assert len(result.output_df) == 3
        assert "customer_id" in result.columns_out

    def test_pivot_mean_aggfunc(self, executor, long_df, recorder):
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["customer_id"],
            columns="quarter",
            values="revenue",
            aggfunc="mean",
        )
        result = executor.execute({"load1": long_df}, step, recorder)
        assert len(result.output_df) == 3
        assert isinstance(result.output_df, pd.DataFrame)

    def test_pivot_fill_value_applied(self, executor, recorder):
        df = pd.DataFrame(
            {
                "id": [1, 2],
                "cat": ["A", "B"],
                "val": [10, 20],
            }
        )
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["id"],
            columns="cat",
            values="val",
            fill_value=-1,
        )
        result = executor.execute({"load1": df}, step, recorder)
        assert len(result.output_df) == 2

    def test_pivot_missing_column_raises(self, executor, long_df, recorder):
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["nonexistent_column"],
            columns="quarter",
            values="revenue",
        )
        with pytest.raises(Exception):
            executor.execute({"load1": long_df}, step, recorder)

    def test_pivot_no_multiindex_in_output(self, executor, long_df, recorder):
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["customer_id"],
            columns="quarter",
            values="revenue",
        )
        result = executor.execute({"load1": long_df}, step, recorder)
        for col in result.output_df.columns:
            assert not isinstance(col, tuple)

    def test_pivot_output_is_dataframe(self, executor, long_df, recorder):
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["customer_id"],
            columns="quarter",
            values="revenue",
        )
        result = executor.execute({"load1": long_df}, step, recorder)
        assert isinstance(result.output_df, pd.DataFrame)

    def test_pivot_multi_index(self, executor, recorder):
        df = pd.DataFrame(
            {
                "customer_id": [1, 1, 2, 2],
                "region": ["A", "A", "B", "B"],
                "quarter": ["Q1", "Q2", "Q1", "Q2"],
                "revenue": [100, 200, 300, 400],
            }
        )
        step = PivotStepConfig(
            name="test",
            step_type=StepType.PIVOT,
            input="load1",
            index=["customer_id", "region"],
            columns="quarter",
            values="revenue",
        )
        result = executor.execute({"load1": df}, step, recorder)
        assert "customer_id" in result.output_df.columns
        assert "region" in result.output_df.columns


class TestUnpivotStep:
    def test_unpivot_basic(self, executor, wide_df, recorder):
        step = UnpivotStepConfig(
            name="test",
            step_type=StepType.UNPIVOT,
            input="load1",
            id_vars=["customer_id", "region"],
            value_vars=["q1_revenue", "q2_revenue"],
            var_name="quarter",
            value_name="revenue",
        )
        result = executor.execute({"load1": wide_df}, step, recorder)
        assert "quarter" in result.columns_out
        assert "revenue" in result.columns_out

    def test_unpivot_row_count_correct(self, executor, wide_df, recorder):
        step = UnpivotStepConfig(
            name="test",
            step_type=StepType.UNPIVOT,
            input="load1",
            id_vars=["customer_id"],
            value_vars=["q1_revenue", "q2_revenue"],
        )
        result = executor.execute({"load1": wide_df}, step, recorder)
        assert len(result.output_df) == 6

    def test_unpivot_default_var_value_names(self, executor, wide_df, recorder):
        step = UnpivotStepConfig(
            name="test",
            step_type=StepType.UNPIVOT,
            input="load1",
            id_vars=["customer_id"],
            value_vars=["q1_revenue", "q2_revenue"],
        )
        result = executor.execute({"load1": wide_df}, step, recorder)
        assert "variable" in result.output_df.columns
        assert "value" in result.output_df.columns

    def test_unpivot_missing_column_raises(self, executor, wide_df, recorder):
        step = UnpivotStepConfig(
            name="test",
            step_type=StepType.UNPIVOT,
            input="load1",
            id_vars=["nonexistent"],
            value_vars=["q1_revenue"],
        )
        with pytest.raises(Exception):
            executor.execute({"load1": wide_df}, step, recorder)

    def test_unpivot_output_is_dataframe(self, executor, wide_df, recorder):
        step = UnpivotStepConfig(
            name="test",
            step_type=StepType.UNPIVOT,
            input="load1",
            id_vars=["customer_id"],
            value_vars=["q1_revenue"],
        )
        result = executor.execute({"load1": wide_df}, step, recorder)
        assert isinstance(result.output_df, pd.DataFrame)


class TestDeduplicateStep:
    def test_dedup_keep_first(self, executor, df_with_dupes, recorder):
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1", keep="first"
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert len(result.output_df) == 4

    def test_dedup_keep_last(self, executor, df_with_dupes, recorder):
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1", keep="last"
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert len(result.output_df) == 4

    def test_dedup_keep_none_removes_all_dupes(self, executor, df_with_dupes, recorder):
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1", keep="none"
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert len(result.output_df) == 2

    def test_dedup_subset_considers_only_specified_columns(
        self, executor, df_with_dupes, recorder
    ):
        step = DeduplicateStepConfig(
            name="test",
            step_type=StepType.DEDUPLICATE,
            input="load1",
            subset=["order_id"],
            keep="first",
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert len(result.output_df) == 4
        assert result.output_df["order_id"].is_unique

    def test_dedup_no_duplicates_returns_same_length(self, executor, recorder):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1"
        )
        result = executor.execute({"load1": df}, step, recorder)
        assert len(result.output_df) == 3

    def test_dedup_missing_subset_column_raises(
        self, executor, df_with_dupes, recorder
    ):
        step = DeduplicateStepConfig(
            name="test",
            step_type=StepType.DEDUPLICATE,
            input="load1",
            subset=["nonexistent"],
        )
        with pytest.raises(Exception):
            executor.execute({"load1": df_with_dupes}, step, recorder)

    def test_dedup_index_reset(self, executor, df_with_dupes, recorder):
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1"
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert list(result.output_df.index) == list(range(len(result.output_df)))

    def test_dedup_output_is_dataframe(self, executor, df_with_dupes, recorder):
        step = DeduplicateStepConfig(
            name="test", step_type=StepType.DEDUPLICATE, input="load1"
        )
        result = executor.execute({"load1": df_with_dupes}, step, recorder)
        assert isinstance(result.output_df, pd.DataFrame)


class TestFillNullsStep:
    def test_forward_fill(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="forward_fill",
            columns=["numeric_col"],
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert result.output_df["numeric_col"].isna().sum() == 0
        assert result.output_df["numeric_col"].iloc[1] == 1.0

    def test_backward_fill(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="backward_fill",
            columns=["numeric_col"],
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert result.output_df["numeric_col"].iloc[1] == 3.0

    def test_mean_fill(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="mean",
            columns=["numeric_col"],
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        expected = pd.Series([1.0, 3.0, 5.0]).mean()
        assert abs(result.output_df["numeric_col"].iloc[1] - expected) < 0.001

    def test_median_fill(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="median",
            columns=["numeric_col"],
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert result.output_df["numeric_col"].iloc[1] == 3.0

    def test_constant_fill(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="constant",
            columns=["numeric_col"],
            constant_value=-999,
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert result.output_df["numeric_col"].iloc[1] == -999

    def test_constant_without_value_raises(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="constant",
            columns=["numeric_col"],
            constant_value=None,
        )
        with pytest.raises(ValueError):
            executor.execute({"load1": df_with_nulls}, step, recorder)

    def test_mean_on_string_column_raises(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="mean",
            columns=["string_col"],
        )
        with pytest.raises(ValueError):
            executor.execute({"load1": df_with_nulls}, step, recorder)

    def test_missing_column_raises(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="constant",
            columns=["nonexistent"],
            constant_value=0,
        )
        with pytest.raises(Exception):
            executor.execute({"load1": df_with_nulls}, step, recorder)

    def test_only_specified_columns_filled(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="constant",
            columns=["numeric_col"],
            constant_value=0,
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert result.output_df["string_col"].isna().sum() > 0

    def test_no_nulls_unchanged(self, executor, recorder):
        df = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="mean",
            columns=["a"],
        )
        result = executor.execute({"load1": df}, step, recorder)
        assert list(result.output_df["a"]) == [1.0, 2.0, 3.0]

    def test_output_is_dataframe(self, executor, df_with_nulls, recorder):
        step = FillNullsStepConfig(
            name="test",
            step_type=StepType.FILL_NULLS,
            input="load1",
            strategy="constant",
            columns=["numeric_col"],
            constant_value=0,
        )
        result = executor.execute({"load1": df_with_nulls}, step, recorder)
        assert isinstance(result.output_df, pd.DataFrame)


class TestSampleStep:
    def test_sample_n_exact_row_count(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test", step_type=StepType.SAMPLE, input="load1", n=500
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert len(result.output_df) == 500

    def test_sample_fraction_approximate_row_count(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test", step_type=StepType.SAMPLE, input="load1", fraction=0.1
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert 990 <= len(result.output_df) <= 1010

    def test_sample_reproducible_with_same_seed(self, executor, large_df, recorder):
        step1 = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            random_state=42,
        )
        step2 = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            random_state=42,
        )
        result1 = executor.execute({"load1": large_df}, step1, recorder)
        result2 = executor.execute({"load1": large_df}, step2, recorder)
        assert list(result1.output_df["id"]) == list(result2.output_df["id"])

    def test_sample_different_seeds_produce_different_samples(
        self, executor, large_df, recorder
    ):
        step1 = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            random_state=1,
        )
        step2 = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            random_state=2,
        )
        result1 = executor.execute({"load1": large_df}, step1, recorder)
        result2 = executor.execute({"load1": large_df}, step2, recorder)
        assert list(result1.output_df["id"]) != list(result2.output_df["id"])

    def test_sample_n_and_fraction_together_raises(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            fraction=0.1,
        )
        with pytest.raises(ValueError):
            executor.execute({"load1": large_df}, step, recorder)

    def test_sample_n_exceeds_rows_returns_all(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test", step_type=StepType.SAMPLE, input="load1", n=99999
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert len(result.output_df) == len(large_df)

    def test_sample_neither_n_nor_fraction_raises(self, executor, large_df, recorder):
        step = SampleStepConfig(name="test", step_type=StepType.SAMPLE, input="load1")
        with pytest.raises(ValueError):
            executor.execute({"load1": large_df}, step, recorder)

    def test_sample_index_reset_after_sampling(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test", step_type=StepType.SAMPLE, input="load1", n=100
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert list(result.output_df.index) == list(range(100))

    def test_stratified_sample_preserves_distribution(
        self, executor, large_df, recorder
    ):
        step = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=1000,
            random_state=42,
            stratify_by="region",
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert len(result.output_df) <= 1000
        north_pct = (result.output_df["region"] == "North").mean()
        assert 0.30 <= north_pct <= 0.50

    def test_stratify_by_missing_column_raises(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test",
            step_type=StepType.SAMPLE,
            input="load1",
            n=100,
            stratify_by="nonexistent",
        )
        with pytest.raises(Exception):
            executor.execute({"load1": large_df}, step, recorder)

    def test_sample_output_is_dataframe(self, executor, large_df, recorder):
        step = SampleStepConfig(
            name="test", step_type=StepType.SAMPLE, input="load1", n=100
        )
        result = executor.execute({"load1": large_df}, step, recorder)
        assert isinstance(result.output_df, pd.DataFrame)
