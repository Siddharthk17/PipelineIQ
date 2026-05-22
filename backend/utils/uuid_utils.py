"""UUID utility functions shared across API modules.

Provides centralized UUID validation and conversion to avoid
code duplication across pipelines.py, files.py, lineage.py, and webhooks.py.
"""

import uuid

from fastapi import HTTPException, status


def validate_uuid_format(value: str) -> None:
    """Raise 422 if the value is not a valid UUID."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID format: '{value}'",
        )


def as_uuid(val) -> uuid.UUID:
    """Convert str or uuid.UUID to uuid.UUID for DB queries.
    Raises HTTPException(422) on invalid format.
    """
    if isinstance(val, uuid.UUID):
        return val
    validate_uuid_format(str(val))
    return uuid.UUID(str(val))
