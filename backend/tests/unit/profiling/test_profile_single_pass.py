"""M-142: profiling completeness must come from the single profile pass.

profile_dataframe() already computes per-column null_count; running
compute_completeness(df) afterwards repeats a full isna pass over the
frame (~6ms/200k rows). The derived value must be exactly equal.
"""
import pandas as pd

from backend.profiling.analyzer import compute_completeness, profile_dataframe
from backend.tasks.profiling import _completeness_from_profile


def _check(df):
    profile = profile_dataframe(df)
    assert _completeness_from_profile(
        profile, len(df)) == compute_completeness(df)


def test_completeness_matches_no_nulls():
    _check(pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}))


def test_completeness_matches_with_nulls():
    _check(pd.DataFrame({
        "a": [1, None, 3, None],
        "b": [None, None, "y", "z"],
        "c": [1.5, 2.5, None, 4.5],
    }))


def test_completeness_matches_all_null_column():
    _check(pd.DataFrame({"a": [None, None], "b": [1, 2]}))


def test_completeness_empty_frame():
    df = pd.DataFrame()
    assert _completeness_from_profile({}, 0) == compute_completeness(df) == 100.0
