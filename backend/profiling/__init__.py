"""Automatic data profiling engine."""

from backend.profiling.analyzer import (
    compute_completeness,
    compute_histogram,
    detect_semantic_flags,
    infer_semantic_type,
    profile_dataframe,
)

__all__ = [
    "compute_completeness",
    "compute_histogram",
    "detect_semantic_flags",
    "infer_semantic_type",
    "profile_dataframe",
]
