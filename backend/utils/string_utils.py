"""String utility functions for PipelineIQ.

Provides fuzzy column name matching, pipeline name sanitization,
and string truncation used throughout the pipeline engine.
"""

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

    Uses difflib.get_close_matches to suggest the most likely intended
    column name when a user makes a typo in their pipeline configuration.
    """
    matches = difflib.get_close_matches(target, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else None


def sanitize_pipeline_name(name: str) -> str:
    """Convert a pipeline name to a safe identifier.

    Strips leading/trailing whitespace, replaces non-alphanumeric characters
    with underscores, collapses consecutive underscores, and lowercases
    the result.
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
    """
    return bool(_IDENTIFIER_PATTERN.match(name))


def is_safe_filename(filename: str) -> bool:
    """Check if a filename is safe and doesn't contain path traversal attempts.

    Returns False if filename contains '..' or any path separators.
    """
    if not filename:
        return False
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return True


def truncate_string(
    text: str,
    max_length: int,
    suffix: str = "...",
) -> str:
    """Truncate a string to max_length, appending suffix if truncated.

    The suffix length is accounted for so the total result never
    exceeds max_length.

    Raises:
        ValueError: If max_length is less than the suffix length.
    """
    if max_length < len(suffix):
        raise ValueError(
            f"max_length ({max_length}) must be >= suffix length ({len(suffix)})"
        )
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix
