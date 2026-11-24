"""Regression tests for M-082: server_default quoting breaks fresh PG create.

Root cause: plain-string ``server_default`` values are rendered as quoted
string literals. ``"'warn'"`` becomes ``DEFAULT '''warn'''`` (triple-quote),
which PostgreSQL rejects with ``invalid input value for enum`` — so
``AUTO_CREATE_TABLES`` / ``Base.metadata.create_all`` cannot create the
schema on a fresh database. Plain ``"true"``/``"false"`` booleans render as
``DEFAULT 'true'`` (wrong type, only tolerated by implicit cast).
"""

import os

os.environ.setdefault("ENVIRONMENT", "test")

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

import backend.models  # noqa: F401 (model registration side effect)
from backend.database import Base


def _pg_ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect()))


def test_no_triple_quoted_defaults_in_pg_ddl():
    """No table may render '''-quoted server defaults for PostgreSQL."""
    offenders = []
    for table in Base.metadata.tables.values():
        ddl = _pg_ddl(table)
        if "'''" in ddl:
            offenders.append(table.name)
    assert not offenders, f"triple-quoted defaults in: {offenders}"


def test_enum_defaults_are_valid_enum_labels():
    """Enum column defaults must be plain labels PG accepts."""
    from backend.models import PipelineContract

    ddl = _pg_ddl(PipelineContract.__table__)
    assert "DEFAULT 'warn'" in ddl
    assert "'''warn'''" not in ddl


def test_jsonb_defaults_render_without_triple_quotes():
    """JSONB '{}' / '[]' defaults must not be triple-quoted."""
    from backend.models import DataAsset, PipelineContract

    for table in (DataAsset.__table__, PipelineContract.__table__):
        ddl = _pg_ddl(table)
        assert "'''" not in ddl, table.name


def test_boolean_defaults_unquoted():
    """Boolean defaults must render as DEFAULT true/false, not 'true'."""
    from backend.models import Webhook

    ddl = _pg_ddl(Webhook.__table__)
    assert "DEFAULT true" in ddl
    assert "DEFAULT 'true'" not in ddl
