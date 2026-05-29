"""Unit tests for the data profiling engine."""

import pytest
import pandas as pd
import numpy as np
from backend.profiling.analyzer import (
    profile_dataframe,
    infer_semantic_type,
    detect_semantic_flags,
    compute_completeness,
    compute_histogram,
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
        assert infer_semantic_type(
            pd.Series([1, 2, 3, 4]), "count") == "numeric"

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

    def test_numeric_column_has_all_required_fields(self, numeric_series):
        profile = profile_dataframe(pd.DataFrame({"val": numeric_series}))
        required = {"name", "null_count", "null_pct", "unique_count",
                    "semantic_type", "flags", "min", "max", "mean",
                    "median", "std_dev", "p25", "p75", "outlier_count", "histogram"}
        assert required.issubset(profile["val"].keys())

    def test_categorical_column_has_top_values_fields(self, categorical_series):
        profile = profile_dataframe(pd.DataFrame({"col": categorical_series}))
        assert "top_values" in profile["col"]
        assert "avg_length" in profile["col"]
        assert "max_length" in profile["col"]

    def test_single_row_dataframe_profiled_correctly(self):
        df = pd.DataFrame({"a": [42], "b": ["hello"]})
        profile = profile_dataframe(df)
        assert profile["a"]["null_count"] == 0
        assert profile["b"]["top_values"][0]["value"] == "hello"


class TestProfilerEdgeCases:
    def test_all_null_column_handled_gracefully(self):
        s = pd.Series([None, None, None, None], dtype=object)
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["null_count"] == 4
        assert profile["col"]["null_pct"] == 100.0
        assert profile["col"]["unique_count"] == 0

    def test_completeness_0_when_all_null(self):
        df = pd.DataFrame({"a": [None, None], "b": [None, None]})
        assert compute_completeness(df) == 0.0

    def test_no_high_null_flag_when_under_20_pct(self):
        s = pd.Series([None] * 10 + [1] * 90)
        flags = detect_semantic_flags(s, "field")
        assert "high_null_rate" not in flags

    def test_constant_flag_when_one_unique_value(self):
        s = pd.Series(["USA"] * 100)
        flags = detect_semantic_flags(s, "country")
        assert "constant" in flags

    def test_boolean_flag_when_two_values(self):
        s = pd.Series([True, False, True, False, True])
        flags = detect_semantic_flags(s, "is_active")
        assert "likely_boolean" in flags

    def test_high_cardinality_flag(self):
        s = pd.Series([f"value_{i}" for i in range(1000)])
        flags = detect_semantic_flags(s, "unique_col")
        assert "high_cardinality" in flags

    def test_no_outliers_in_uniform_data(self):
        s = pd.Series(range(100))
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["outlier_count"] == 0

    def test_histogram_counts_sum_to_total_non_null(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        total_in_bins = sum(b["count"] for b in profile["col"]["histogram"])
        assert total_in_bins == 5

    def test_histogram_bucket_structure(self, numeric_series):
        profile = profile_dataframe(pd.DataFrame({"val": numeric_series}))
        for bucket in profile["val"]["histogram"]:
            assert "bin_start" in bucket
            assert "bin_end" in bucket
            assert "count" in bucket
            assert isinstance(bucket["count"], int)

    def test_std_dev_correct(self):
        s = pd.Series([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        actual_std = np.std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0], ddof=1)
        assert abs(profile["col"]["std_dev"] - actual_std) < 0.01

    def test_avg_length_correct(self):
        s = pd.Series(["ab", "abcd", "abcdef"])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["avg_length"] == 4.0

    def test_max_length_correct(self):
        s = pd.Series(["short", "a much longer string", "medium len"])
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert profile["col"]["max_length"] == len("a much longer string")

    def test_p25_p75_correct(self):
        s = pd.Series(range(100))
        profile = profile_dataframe(pd.DataFrame({"col": s}))
        assert 24 <= profile["col"]["p25"] <= 25
        assert 74 <= profile["col"]["p75"] <= 75

    def test_empty_series_flags_returns_empty_list(self):
        s = pd.Series([], dtype=float)
        flags = detect_semantic_flags(s, "empty_col")
        assert flags == []

    def test_histogram_empty_series_returns_empty_list(self):
        s = pd.Series([], dtype=float)
        result = compute_histogram(s)
        assert result == []

    def test_float_column_is_numeric_type(self):
        assert infer_semantic_type(pd.Series([1.1, 2.2, 3.3]), "measurement") == "numeric"

    def test_boolean_values_string_detected(self):
        s = pd.Series(["true", "false", "true", "false"])
        result = infer_semantic_type(s, "is_active")
        assert result == "boolean"

    def test_url_column_detected_by_name(self):
        s = pd.Series(["https://example.com", "https://test.org"])
        result = infer_semantic_type(s, "url")
        assert result == "url"

    def test_id_column_detected_by_pattern_and_name(self):
        import uuid
        s = pd.Series([str(uuid.uuid4()) for _ in range(10)])
        result = infer_semantic_type(s, "record_uuid")
        assert result == "identifier"
