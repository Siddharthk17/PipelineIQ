"""Enum mapping invariants for PostgreSQL enum compatibility."""

import importlib.util
from pathlib import Path

from sqlalchemy.dialects import postgresql

from backend.models import NotificationConfig, PipelinePermission


def test_notification_enum_persists_lowercase_values():
    """Notification enum should persist lowercase values expected by migration type."""
    assert NotificationConfig.__table__.c.type.type.enums == ["slack", "email"]


def test_permission_enum_persists_lowercase_values():
    """Permission enum should persist lowercase values expected by migration type."""
    assert PipelinePermission.__table__.c.permission_level.type.enums == [
        "owner",
        "runner",
        "viewer",
    ]


def test_healing_attempt_migration_uses_non_duplicating_postgres_enum():
    """Healing attempt migration must avoid implicit enum recreation in create_table."""
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "9f3c1b2a4d5e_add_healing_attempts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "healing_attempts_migration",
        migration_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    healing_enum = module._healing_status_enum("postgresql")
    assert isinstance(healing_enum, postgresql.ENUM)
    assert healing_enum.create_type is False
