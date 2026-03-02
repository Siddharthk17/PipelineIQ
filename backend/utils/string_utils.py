"""String utility functions for PipelineIQ.

Provides fuzzy column name matching, pipeline name sanitization,
and string truncation used throughout the pipeline engine.
"""

# Standard library
import difflib
import re
from typing import List, Optional


# Regex pattern for valid pipeline/step identifiers: alphanumeric + underscores
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9_]")


def find_closest_column(
    target: str,
    candidates: List[str],
    cutoff: float = 0.6,
) -> Optional[str]:
    """Find the closest column name using fuzzy string matching.

    Uses difflib.get_close_matches with SequenceMatcher ratio scoring
    to suggest the most likely intended column name when a user makes
    a typo in their pipeline configuration.

    Args:
        target: The column name to find a match for.
        candidates: List of available column names to match against.
        cutoff: Minimum similarity ratio (0.0–1.0) to consider a match.
            Defaults to 0.6, which catches common typos like "amunt" → "amount".

    Returns:
        The closest matching column name, or None if no match exceeds
        the cutoff threshold.

    Example:
        >>> find_closest_column("amunt", ["amount", "status", "date"])
        'amount'
        >>> find_closest_column("xyz", ["amount", "status", "date"])
        None
    """
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def sanitize_pipeline_name(name: str) -> str:
    """Convert a pipeline name to a safe identifier.

    Strips leading/trailing whitespace, replaces non-alphanumeric characters
    with underscores, collapses consecutive underscores, and lowercases
    the result.

    Args:
        name: The raw pipeline name from user input.

    Returns:
        A sanitized string safe for use as an identifier.

    Example:
        >>> sanitize_pipeline_name("My Pipeline! (v2)")
        'my_pipeline__v2_'
    """
    stripped = name.strip().lower()
    sanitized = _SANITIZE_PATTERN.sub("_", stripped)
    # Collapse consecutive underscores
    collapsed = re.sub(r"_+", "_", sanitized)
    return collapsed.strip("_")


def is_valid_identifier(name: str) -> bool:
    """Check if a string is a valid pipeline or step identifier.

    Valid identifiers start with a letter or underscore and contain
    only alphanumeric characters and underscores.

    Args:
        name: The string to validate.

    Returns:
        True if the string is a valid identifier, False otherwise.
    """
    return bool(_IDENTIFIER_PATTERN.match(name))


def truncate_string(
    text: str,
    max_length: int,
    suffix: str = "...",
) -> str:
    """Truncate a string to max_length, appending suffix if truncated.

    If the string is already within the limit, it is returned unchanged.
    The suffix length is accounted for so the total result length never
    exceeds max_length.

    Args:
        text: The string to truncate.
        max_length: Maximum allowed length of the result (including suffix).
        suffix: String appended when truncation occurs. Defaults to "...".

    Returns:
        The original string if within limit, or a truncated version with suffix.

    Raises:
        ValueError: If max_length is less than the suffix length.

    Example:
        >>> truncate_string("Hello World", 8)
        'Hello...'
        >>> truncate_string("Hi", 8)
        'Hi'
    """
    if max_length < len(suffix):
        raise ValueError(
            f"max_length ({max_length}) must be >= suffix length ({len(suffix)})"
        )
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
