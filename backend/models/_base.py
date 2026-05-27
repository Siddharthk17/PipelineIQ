"""Shared SQLAlchemy utilities for all model modules.

One canonical location for PgJSONB, _generate_uuid, and _enum_values
so that ``models.py`` and any sub-module share the same definitions.
"""

import uuid
from enum import Enum as PyEnum

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from backend.database import Base

# Dialect-aware JSONB: native JSONB on PostgreSQL, JSON fallback on SQLite
PgJSONB = JSONB().with_variant(JSON(), "sqlite")


def _enum_values(enum_class: type[PyEnum]) -> list[str]:
    """Persist Python Enum values (not names) in database columns."""
    return [member.value for member in enum_class]


def _generate_uuid() -> uuid.UUID:
    return uuid.uuid4()
