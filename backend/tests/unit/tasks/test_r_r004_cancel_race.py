"""Regression test for R-R-004 / R-R-009: a concurrent user cancel must win
over a worker that crashes afterwards (no FAILED clobber of CANCELLED).

Runs against throwaway PostgreSQL because the lost-cancel guard uses
SELECT ... FOR UPDATE, which SQLite ignores. Skipped when PG is absent.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PipelineRun, PipelineStatus, User

PG_URL = (
    "postgresql://postgres:pipelineiq_dev_password@localhost:5432/pipelineiq"
)


def _pg_session_factory():
    eng = create_engine(PG_URL)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng)


def _seed(db):
    user = User(
        id=uuid.uuid4(), email=f"rr4_{uuid.uuid4().hex[:8]}@t.local",
        username=f"rr4_{uuid.uuid4().hex[:8]}", hashed_password="x",
        role="viewer", is_active=True)
    db.add(user)
    db.flush()
    run = PipelineRun(
        id=uuid.uuid4(), name="cancel-race",
        status=PipelineStatus.PENDING,
        yaml_config="pipeline:\n  name: cancel-race\n  steps: []\n",
        user_id=user.id,
    )
    db.add(run)
    db.commit()
    return run


def _pg_available():
    try:
        eng = create_engine(PG_URL)
        with eng.connect():
            pass
        return True
    except Exception:
        return False
    else:
        return True


def test_exception_after_concurrent_cancel_keeps_cancelled(monkeypatch):
    if not _pg_available():
        pytest.skip("throwaway PG unavailable")
    import backend.tasks.pipeline_tasks as pt

    Session = _pg_session_factory()

    def boom(*args, **kwargs):
        # Concurrent user cancel lands while the worker is "executing",
        # through a separate transaction exactly like the API would use.
        other = Session()
        victim = other.query(PipelineRun).filter(
            PipelineRun.id == run_row_id[0]).one()
        victim.status = PipelineStatus.CANCELLED
        other.commit()
        other.close()
        raise RuntimeError("boom")

    monkeypatch.setattr(pt, "SessionLocal", Session)
    monkeypatch.setattr(pt, "_execute_with_autonomous_healing", boom)
    monkeypatch.setattr(pt, "_publish_terminal_event", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.tasks.notification_tasks.deliver_notifications_task.delay",
        lambda **kw: None,
    )
    monkeypatch.setattr(
        "backend.tasks.webhook_tasks.deliver_webhooks_task.delay",
        lambda **kw: None,
    )

    db = Session()
    run = _seed(db)
    run_row_id = [run.id]
    db.close()

    with pytest.raises(RuntimeError):
        pt.execute_pipeline_task.apply(args=[str(run.id)]).get()

    check = Session()
    final = check.query(PipelineRun).filter(
        PipelineRun.id == run.id).one().status
    check.close()
    assert final == PipelineStatus.CANCELLED, (
        f"concurrent cancel was clobbered to {final}")
