"""Classify pipeline failures as healable schema drift or hard failures."""

from __future__ import annotations

from typing import Optional


HEALABLE_ERROR_TYPES: dict[str, str] = {
    "ColumnNotFoundError": "column_renamed_or_removed",
    "JoinKeyMissingError": "join_key_missing_in_schema",
    "KeyError": "column_renamed_or_removed",
    "MergeError": "join_key_missing_in_schema",
    "ValueError": "type_mismatch_or_invalid_operation",
    "TypeError": "type_mismatch_or_invalid_operation",
    "AggregationError": "type_mismatch_or_invalid_operation",
}

HEALABLE_ERROR_MESSAGE_PATTERNS: tuple[str, ...] = (
    "not found in columns",
    "column not found",
    "missing column",
    "does not exist",
    "join key",
    "key not found",
    "cannot convert",
    "type mismatch",
    "invalid value",
)

NOT_HEALABLE_ERROR_MESSAGE_PATTERNS: tuple[str, ...] = (
    "division by zero",
    "index out of range",
    "out of memory",
    "memory error",
    "permission denied",
    "connection refused",
    "timeout",
    "syntax error",
    "import error",
    "module not found",
    "attribute error",
    "file not found",
    "failed to read file",
    "unsupported file format",
)


def is_healable(error: Exception) -> bool:
    """Return True when a failure looks like schema drift we can patch safely."""
    error_type_name = type(error).__name__
    error_message = str(error).lower()

    if any(marker in error_message for marker in NOT_HEALABLE_ERROR_MESSAGE_PATTERNS):
        return False

    if error_type_name not in HEALABLE_ERROR_TYPES:
        return False

    if error_type_name in {"ValueError", "TypeError", "AggregationError"}:
        return any(marker in error_message for marker in HEALABLE_ERROR_MESSAGE_PATTERNS)

    return True


def get_healing_scenario(error: Exception) -> Optional[str]:
    """Return the human-readable healing scenario when an error is healable."""
    if not is_healable(error):
        return None
    return HEALABLE_ERROR_TYPES.get(type(error).__name__, "schema_drift_detected")
