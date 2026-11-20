"""Regression tests for the stuck-run reconciler (S01, S14).

A run wedged in RUNNING with a dead Celery task must converge to TIMEOUT
(with a terminal event); a run whose task still looks alive must be left
alone, even when old.
"""

from datetime import timedelta
from unittest.mock import patch

from backend.models import PipelineRun, PipelineStatus, User
from backend.tasks import reconciliation as recon
from backend.utils.time_utils import utcnow


def _user_and_run(test_db, status, age_minutes, task_id="celery-task-1"):
    import uuid

    user = User(
        id=uuid.uuid4(), email=f"r_{uuid.uuid4().hex[:8]}@t.local",
        username=f"r_{uuid.uuid4().hex[:8]}", hashed_password="x",
        role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    run = PipelineRun(
        id=uuid.uuid4(), name="stuck_pipe", status=status,
        yaml_config="pipeline:\n  name: stuck_pipe\n  steps: []",
        user_id=user.id, celery_task_id=task_id,
        created_at=utcnow() - timedelta(minutes=age_minutes),
        started_at=utcnow() - timedelta(minutes=age_minutes),
    )
    test_db.add(run)
    test_db.commit()
    return run


def test_wedged_run_times_out(test_db):
    run = _user_and_run(test_db, PipelineStatus.RUNNING, 60, "dead-task")
    with patch.object(recon, "_task_state", return_value="FAILURE"), patch(
        "backend.tasks.reconciliation._publish_terminal_event",
        create=True,
    ):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 1, "timed_out": 1}
    test_db.refresh(run)
    assert run.status == PipelineStatus.TIMEOUT
    assert run.completed_at is not None


def test_live_task_run_is_left_alone(test_db):
    run = _user_and_run(test_db, PipelineStatus.RUNNING, 60, "live-task")
    with patch.object(recon, "_task_state", return_value="STARTED"):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 1, "timed_out": 0}
    test_db.refresh(run)
    assert run.status == PipelineStatus.RUNNING


def test_fresh_run_is_left_alone(test_db):
    run = _user_and_run(test_db, PipelineStatus.RUNNING, 5, "dead-task")
    with patch.object(recon, "_task_state", return_value="FAILURE"):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 0, "timed_out": 0}
    test_db.refresh(run)
    assert run.status == PipelineStatus.RUNNING


def test_completed_run_untouched(test_db):
    run = _user_and_run(test_db, PipelineStatus.COMPLETED, 120, "old-task")
    with patch.object(recon, "_task_state", return_value="FAILURE"):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 0, "timed_out": 0}
    test_db.refresh(run)
    assert run.status == PipelineStatus.COMPLETED


def test_streaming_active_dead_task_times_out(test_db):
    """R-R-003: dead streaming daemon must not wedge the run forever."""
    run = _user_and_run(test_db, PipelineStatus.STREAMING_ACTIVE, 60, "dead-daemon")
    with patch.object(recon, "_task_state", return_value="FAILURE"), patch(
        "backend.tasks.reconciliation._publish_terminal_event",
        create=True,
    ):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 1, "timed_out": 1}
    test_db.refresh(run)
    assert run.status == PipelineStatus.TIMEOUT


def test_streaming_active_live_task_left_alone(test_db):
    run = _user_and_run(test_db, PipelineStatus.STREAMING_ACTIVE, 600, "live-daemon")
    with patch.object(recon, "_task_state", return_value="STARTED"):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 1, "timed_out": 0}
    test_db.refresh(run)
    assert run.status == PipelineStatus.STREAMING_ACTIVE


def test_streaming_active_unknown_task_left_alone(test_db):
    """Indeterminate broker/task state must never reap a live stream."""
    run = _user_and_run(test_db, PipelineStatus.STREAMING_ACTIVE, 600, "mystery-task")
    with patch.object(recon, "_task_state", return_value=None), patch.object(
        recon, "_is_task_known_to_workers", return_value=None
    ):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result == {"checked": 1, "timed_out": 0}
    test_db.refresh(run)
    assert run.status == PipelineStatus.STREAMING_ACTIVE


def test_streaming_paused_never_reaped(test_db):
    run = _user_and_run(test_db, PipelineStatus.STREAMING_PAUSED, 600, "dead-daemon")
    with patch.object(recon, "_task_state", return_value="FAILURE"):
        result = recon.reconcile_stuck_runs(
            db=test_db, timeout=timedelta(minutes=30))
    assert result["timed_out"] == 0
    test_db.refresh(run)
    assert run.status == PipelineStatus.STREAMING_PAUSED
