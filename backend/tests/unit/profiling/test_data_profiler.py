"""Unit tests for the data profiling engine."""

import pytest
import pandas as pd
from backend.profiling.analyzer import (
    profile_dataframe,
    infer_semantic_type,
    detect_semantic_flags,
    compute_histogram,
    compute_completeness,
)


@pytest.fixture
def numeric_series():
    return pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])


@pytest.fixture
def categorical_series():
    return pd.Series(["apple", "banana", "apple", "cherry", "banana", "apple"])


@pytest.fixture
def mixed_df():
    return pd.DataFrame(
        {
            "customer_id": [1, 2, 3, 4, 5],
            "revenue": [100.0, 200.0, None, 400.0, 500.0],
            "region": ["North", "South", "North", "West", "South"],
            "email": ["a@b.com", "c@d.com", "e@f.com", None, "g@h.com"],
            "created_at": [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
            ],
        }
    )


class TestNumericColumnProfiling:
    def test_null_count_correct(self):
        s = pd.Series([1.0, 2.0, None, 4.0, None])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["null_count"] == 2

    def test_null_pct_correct(self):
        s = pd.Series([1.0, 2.0, None, 4.0, None])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["null_pct"] == 40.0

    def test_min_max_correct(self, numeric_series):
        profile = profile_dataframe(pd.DataFrame({"val": numeric_series}))
        assert profile["val"]["min"] == 1.0
        assert profile["val"]["max"] == 100.0

    def test_mean_correct(self):
        s = pd.Series([10.0, 20.0, 30.0])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert abs(profile["col"]["mean"] - 20.0) < 0.001

    def test_median_correct(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["median"] == 3.0

    def test_outlier_detection_iqr(self, numeric_series):
        profile = profile_dataframe(pd.DataFrame({"val": numeric_series}))
        assert profile["val"]["outlier_count"] >= 1

    def test_histogram_has_buckets(self, numeric_series):
        profile = profile_dataframe(pd.DataFrame({"val": numeric_series}))
        assert len(profile["val"]["histogram"]) > 0


class TestCategoricalColumnProfiling:
    def test_top_values_max_5(self):
        s = pd.Series(["a", "b", "c", "d", "e", "f", "g", "a", "a"])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert len(profile["col"]["top_values"]) <= 5

    def test_top_values_sorted_by_count_desc(self, categorical_series):
        profile = profile_dataframe(pd.DataFrame({"col": categorical_series}))
        top = profile["col"]["top_values"]
        assert top[0]["value"] == "apple"
        assert top[0]["count"] == 3


class TestSemanticTypeInference:
    def test_integer_column_is_numeric(self):
        assert infer_semantic_type(pd.Series([1, 2, 3, 4]), "count") == "numeric"

    def test_email_column_detected(self):
        s = pd.Series(["user@example.com", "other@test.org", "name@site.net"])
        result = infer_semantic_type(s, "email")
        assert result == "email"

    def test_low_cardinality_string_is_categorical(self):
        s = pd.Series(["North", "South", "East", "West"] * 100)
        result = infer_semantic_type(s, "region")
        assert result == "categorical"


class TestSemanticFlags:
    def test_pii_flag_on_name_column(self):
        s = pd.Series(["Alice", "Bob", "Charlie"])
        flags = detect_semantic_flags(s, "full_name")
        assert "likely_pii" in flags

    def test_id_flag_on_id_column(self):
        s = pd.Series(range(1000))
        flags = detect_semantic_flags(s, "user_id")
        assert "likely_id" in flags

    def test_high_null_flag_when_over_20_pct(self):
        s = pd.Series([None] * 25 + [1] * 75)
        flags = detect_semantic_flags(s, "optional_field")
        assert "high_null_rate" in flags


class TestCompleteness:
    def test_completeness_100_when_no_nulls(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        assert compute_completeness(df) == 100.0

    def test_completeness_50_when_half_null(self):
        df = pd.DataFrame({"a": [1, None], "b": [None, 2]})
        assert compute_completeness(df) == 50.0


class TestFullDataFrameProfiling:
    def test_profile_has_entry_for_every_column(self, mixed_df):
        profile = profile_dataframe(mixed_df)
        for col in mixed_df.columns:
            assert col in profile

    def test_empty_dataframe_returns_empty_profile(self):
        df = pd.DataFrame()
        profile = profile_dataframe(df)
        assert profile == {}
