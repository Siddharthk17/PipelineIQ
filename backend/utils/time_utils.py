"""Time utility functions for PipelineIQ.

Provides timezone-aware UTC timestamps, performance measurement,
and human-readable duration formatting used in step execution
results and API responses.
"""

import time
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime.

    Always use this instead of datetime.utcnow() which returns a naive
    datetime without timezone info.
    """
    return datetime.now(timezone.utc)


def measure_ms(start: float) -> int:
    """Calculate elapsed milliseconds from a time.perf_counter() start value."""
    elapsed_seconds = time.perf_counter() - start
    return int(elapsed_seconds * 1000)


_MS_PER_SECOND: int = 1_000
_MS_PER_MINUTE: int = 60_000
_MS_PER_HOUR: int = 3_600_000


def format_duration(ms: int) -> str:
    """Format milliseconds as a human-readable duration string.

    - Under 1 second: "450ms"
    - Under 1 minute: "1.2s"
    - Under 1 hour:   "2m 3s"
    - Over 1 hour:    "1h 2m 3s"

    Raises:
        ValueError: If ms is negative.
    """
    if ms < 0:
        raise ValueError(f"Duration cannot be negative: {ms}ms")

    if ms < _MS_PER_SECOND:
        return f"{ms}ms"

    if ms < _MS_PER_MINUTE:
        seconds = ms / _MS_PER_SECOND
        return f"{seconds:.1f}s"

    if ms < _MS_PER_HOUR:
        minutes = ms // _MS_PER_MINUTE
        remaining_seconds = (ms % _MS_PER_MINUTE) // _MS_PER_SECOND
        return f"{minutes}m {remaining_seconds}s"

    hours = ms // _MS_PER_HOUR
    remaining_minutes = (ms % _MS_PER_HOUR) // _MS_PER_MINUTE
    remaining_seconds = (ms % _MS_PER_MINUTE) // _MS_PER_SECOND
    return f"{hours}h {remaining_minutes}m {remaining_seconds}s"
