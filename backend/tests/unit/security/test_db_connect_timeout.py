"""Regression test for fail-fast DB connections (S44).

Request-path database work (audit logs, storage events) must not block
for tens of seconds when the database is unreachable. Engines must carry
a bounded connect timeout.
"""

from unittest.mock import patch

import backend.database as database_module


def test_postgres_engine_has_bounded_connect_timeout():
    seen = {}

    def fake_create_engine(url, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")

    with patch.object(
        database_module, "create_engine", side_effect=fake_create_engine
    ):
        try:
            database_module._build_engine(
                "postgresql://u:p@dbhost:5432/db")
        except RuntimeError:
            pass
    connect_args = seen.get("connect_args", {})
    assert "connect_timeout" in connect_args, connect_args
    assert 0 < int(connect_args["connect_timeout"]) <= 10


def test_sqlite_engine_unchanged():
    engine = database_module._build_engine("sqlite:///:memory:")
    assert engine is not None
