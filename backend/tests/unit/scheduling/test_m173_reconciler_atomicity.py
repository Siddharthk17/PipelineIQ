"""Regression tests for M-173 (reconciler): one failed commit must not
wedge the whole sweep; the session is rolled back and remaining runs
are still processed.
"""

from datetime import timedelta
from unittest.mock import patch

from backend.models import PipelineRun, PipelineStatus, User
from backend.tasks import reconciliation as recon
from backend.utils.time_utils import utcnow


def _user_and_run(test_db, name, status, age_minutes, task_id):
    import uuid

    user = User(
        id=uuid.uuid4(), email=f"{name}@t.local",
        username=name, hashed_password="x",
        role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    run = PipelineRun(
        id=uuid.uuid4(), name=f"{name}_pipe", status=status,
        yaml_config="pipeline:\n  name: x\n  steps: []",
        user_id=user.id, celery_task_id=task_id,
        created_at=utcnow() - timedelta(minutes=age_minutes),
        started_at=utcnow() - timedelta(minutes=age_minutes),
    )
    test_db.add(run)
    test_db.commit()
    return run


def test_failed_commit_does_not_abort_sweep(test_db, monkeypatch):
    """A commit failure on one run skips it; others still converge."""
    runs = [
        _user_and_run(
            test_db, f"m173_{i}", PipelineStatus.RUNNING, 60, f"task-{i}")
        for i in range(3)
    ]

    orig_commit = test_db.commit
    calls = []

    def flaky_commit(*args, **kwargs):
        calls.append(1)
        # Fail the commit for the second run only (call order: run0,
        # run1, run2 — log_action uses its own session).
        if len(calls) == 2:
            raise RuntimeError("commit boom")
        return orig_commit(*args, **kwargs)

    monkeypatch.setattr(test_db, "commit", flaky_commit)

    with patch.object(recon, "_task_state", return_value="FAILURE"), patch(
        "backend.tasks.reconciliation._publish_terminal_event",
        create=True,
    ):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))

    assert result == {"checked": 3, "timed_out": 2}
    statuses = [
        test_db.query(PipelineRun).filter_by(id=r.id).one().status
        for r in runs
    ]
    assert statuses[0] is PipelineStatus.TIMEOUT
    assert statuses[1] is PipelineStatus.RUNNING  # rolled back, retried later
    assert statuses[2] is PipelineStatus.TIMEOUT
