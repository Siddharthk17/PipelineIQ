"""Enum mapping invariants for PostgreSQL enum compatibility."""

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
