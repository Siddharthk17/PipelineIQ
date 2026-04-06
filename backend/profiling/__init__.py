"""Automatic data profiling engine."""

from backend.profiling.analyzer import (
    profile_dataframe,
    compute_completeness,
    infer_semantic_type,
    detect_semantic_flags,
    compute_histogram,
)

__all__ = [
    "profile_dataframe",
    "compute_completeness",
    "infer_semantic_type",
    "detect_semantic_flags",
    "compute_histogram",
]
