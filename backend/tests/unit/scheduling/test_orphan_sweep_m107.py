"""Regression tests for M-107: orphaned-upload sweep task.

The periodic sweep deletes ``UploadedFile`` rows that are BOTH older than
the retention horizon AND show evidence of abandonment (storage object
already gone, or the upload never finalized: no snapshot and no profile).
Referenced/live rows — recent, or old but with their object and metadata
intact — must survive. Deletes run children-first (snapshots, profiles,
policies) so the parent-row delete never hits an FK violation, and each
row commits in isolation so one poisoned row cannot wedge the sweep.
"""

import uuid
from datetime import datetime, timedelta

import pytest

from backend.models import (
    ColumnPolicy,
    FileProfile,
    SchemaSnapshot,
    UploadedFile,
    User,
)


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


class _FakeStorage:
    def __init__(self, existing):
        self.existing = set(existing)
        self.deleted = []

    def exists(self, path):
        return path in self.existing

    def delete(self, path):
        self.deleted.append(path)
        self.existing.discard(path)


def _user(db):
    u = User(
        id=uuid.uuid4(),
        email=f"sweep-{uuid.uuid4().hex[:8]}@example.com",
        username=f"sweep-{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        role="viewer",
    )
    db.add(u)
    db.commit()
    return u


def _upload(db, user_id, age_days, path, snapshot=True, profile=False, policy=False):
    row = UploadedFile(
        id=uuid.uuid4(),
        original_filename="data.csv",
        stored_path=path,
        file_size_bytes=10,
        row_count=2,
        column_count=1,
        columns=["a"],
        dtypes={"a": "int64"},
        user_id=user_id,
        created_at=datetime.now() - timedelta(days=age_days),
    )
    db.add(row)
    db.commit()
    if snapshot:
        db.add(
            SchemaSnapshot(
                file_id=row.id, columns=["a"], dtypes={"a": "int64"}, row_count=2
            )
        )
    if profile:
        db.add(
            FileProfile(
                file_id=row.id, profile={}, row_count=2, col_count=1,
                status="completed",
            )
        )
    if policy:
        db.add(
            ColumnPolicy(
                file_id=row.id, column_name="a", policy="masked",
                mask_pattern="default",
            )
        )
    db.commit()
    return row


@pytest.fixture()
def sweep_env(test_db, monkeypatch):
    import backend.tasks.storage_maintenance as sm

    fake_redis = _FakeRedis()
    monkeypatch.setattr(sm, "get_cache_redis", lambda: fake_redis)
    monkeypatch.setattr(sm, "SessionLocal", lambda: test_db)
    storage = _FakeStorage(existing=set())
    monkeypatch.setattr(sm.storage_service_module, "storage_service", storage)
    return sm, fake_redis, storage


class TestOrphanSweep:
    def test_sweeps_old_row_with_missing_object(self, test_db, sweep_env):
        sm, _, storage = sweep_env
        u = _user(test_db)
        row = _upload(test_db, u.id, 40, "uploads/gone.csv")
        row_id = row.id
        assert storage.exists("uploads/gone.csv") is False

        result = sm.sweep_orphaned_uploads()

        assert result["deleted"] == 1
        assert test_db.query(UploadedFile).filter_by(id=row_id).first() is None
        assert (
            test_db.query(SchemaSnapshot).filter_by(file_id=row_id).first() is None
        )
        assert "uploads/gone.csv" in storage.deleted

    def test_keeps_old_row_with_live_object(self, test_db, sweep_env):
        sm, _, storage = sweep_env
        u = _user(test_db)
        storage.existing.add("uploads/live.csv")
        row = _upload(test_db, u.id, 40, "uploads/live.csv")
        row_id = row.id

        result = sm.sweep_orphaned_uploads()

        assert result["deleted"] == 0
        assert test_db.query(UploadedFile).filter_by(id=row_id).first() is not None

    def test_keeps_recent_row_even_with_missing_object(self, test_db, sweep_env):
        sm, _, _ = sweep_env
        u = _user(test_db)
        row = _upload(test_db, u.id, 2, "uploads/fresh.csv")
        row_id = row.id

        result = sm.sweep_orphaned_uploads()

        assert result["deleted"] == 0
        assert test_db.query(UploadedFile).filter_by(id=row_id).first() is not None

    def test_sweeps_never_finalized_old_row_and_children_in_order(
        self, test_db, sweep_env
    ):
        sm, _, storage = sweep_env
        u = _user(test_db)
        storage.existing.add("uploads/partial.csv")
        # No snapshot and no profile: upload never finalized.
        row = _upload(
            test_db, u.id, 45, "uploads/partial.csv", snapshot=False, policy=True
        )
        row_id = row.id

        result = sm.sweep_orphaned_uploads()

        assert result["deleted"] == 1
        assert test_db.query(UploadedFile).filter_by(id=row_id).first() is None
        assert (
            test_db.query(ColumnPolicy).filter_by(file_id=row_id).first() is None
        )
        assert "uploads/partial.csv" in storage.deleted

    def test_keeps_old_finalized_row_with_profile(self, test_db, sweep_env):
        sm, _, storage = sweep_env
        u = _user(test_db)
        storage.existing.add("uploads/good.csv")
        row = _upload(
            test_db, u.id, 60, "uploads/good.csv", snapshot=True, profile=True
        )
        row_id = row.id

        result = sm.sweep_orphaned_uploads()

        assert result["deleted"] == 0
        assert test_db.query(UploadedFile).filter_by(id=row_id).first() is not None

    def test_lock_contention_skips_sweep(self, test_db, sweep_env):
        sm, fake_redis, _ = sweep_env
        fake_redis.store[sm.ORPHAN_SWEEP_LOCK_KEY] = "someone-else"
        u = _user(test_db)
        _upload(test_db, u.id, 40, "uploads/gone.csv")

        result = sm.sweep_orphaned_uploads()

        assert result["locked"] is True
        assert result["deleted"] == 0

    def test_wired_into_scheduler(self):
        from backend.celery_config import beat_schedule, task_routes

        assert beat_schedule["sweep-orphaned-uploads"]["task"] == (
            "tasks.sweep_orphaned_uploads"
        )
        assert task_routes["tasks.sweep_orphaned_uploads"]["queue"] == "bulk"
