"""Regression tests for scheduler resilience (w1-daemon, M-071/M-044/M-131).

Covers the beat-side additions: deterministic (idempotent) dispatch
task ids, backlog reporting beyond the batch limit, and outcome sync for
stranded schedule runs. Uses the hermetic SQLite test DB + fakes.
"""
import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock


def _due_schedule(db, name="beat_pipe", minutes_overdue=5):
    from backend.models import PipelineSchedule
    now = datetime.now(UTC)
    sched = PipelineSchedule(
        id=uuid_module.uuid4(),
        user_id=uuid_module.uuid4(),
        pipeline_name=name,
        yaml_config="pipeline:\n  name: beat_pipe\n  steps: []\n",
        cron_expression="* * * * *",
        is_active=True,
        next_run_at=now - timedelta(minutes=minutes_overdue),
        total_runs=0,
        successful_runs=0,
        failed_runs=0,
        healed_runs=0,
    )
    db.add(sched)
    db.commit()
    return sched


class _FakeLock:
    """Minimal Redis stand-in for the beat lock."""

    def __init__(self):
        self.store = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = (
            value.encode() if isinstance(value, str) else value
        )
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        self.store.pop(key, None)


def _patch_beat(monkeypatch, test_db, fake_task=None):
    import backend.tasks.schedule_tasks as st
    monkeypatch.setattr(st, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(
        st, "get_cache_redis", lambda: _FakeLock())
    task = fake_task if fake_task is not None else MagicMock()
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.execute_pipeline_task", task)
    return st, task


class TestM071IdempotentDispatch:
    def test_dispatch_uses_deterministic_task_id(self, test_db, monkeypatch):
        import backend.tasks.schedule_tasks as st
        from backend.models import ScheduleRun
        sched = _due_schedule(test_db)
        dispatched = []
        task = MagicMock()
        task.apply_async.side_effect = (
            lambda *a, **k: dispatched.append((a, k)))
        _patch_beat(monkeypatch, test_db, fake_task=task)
        result = st.check_schedules()
        assert result["triggered"] == 1
        assert len(dispatched) == 1
        run = (
            test_db.query(ScheduleRun)
            .filter(ScheduleRun.schedule_id == sched.id)
            .one()
        )
        assert dispatched[0][1].get("task_id") == f"sched-{run.id}", (
            "beat redelivery must reuse the same Celery task id")


class TestM131Backlog:
    def test_backlog_reported_not_skipped(self, test_db, monkeypatch):
        import backend.tasks.schedule_tasks as st
        for i in range(3):
            _due_schedule(test_db, name=f"pipe_{i}")
        monkeypatch.setattr(st, "SCHEDULE_CHECK_BATCH_LIMIT", 2)
        task = MagicMock()
        _patch_beat(monkeypatch, test_db, fake_task=task)
        result = st.check_schedules()
        assert result["triggered"] == 2
        assert result["backlog"] == 1, (
            "due schedules beyond the batch limit must be reported")


class TestM044ScheduleRunSync:
    def _linked(self, test_db, run_status, name="sync_pipe"):
        from backend.models import PipelineRun, ScheduleRun
        sched = _due_schedule(test_db, name=name, minutes_overdue=0)
        sched.next_run_at = datetime.now(UTC) + timedelta(hours=1)
        prun = PipelineRun(
            id=uuid_module.uuid4(),
            name=name,
            status=run_status,
            yaml_config="pipeline:\n  name: x\n  steps: []\n",
            user_id=sched.user_id,
            trigger="scheduled",
            schedule_id=sched.id,
        )
        test_db.add(prun)
        test_db.commit()
        srun = ScheduleRun(
            schedule_id=sched.id,
            run_id=prun.id,
            triggered_at=datetime.now(UTC),
            status="RUNNING",
        )
        test_db.add(srun)
        test_db.commit()
        return sched, prun, srun

    def test_terminal_run_synced_once(self, test_db):
        import backend.tasks.schedule_tasks as st
        from backend.models import PipelineStatus
        sched, prun, srun = self._linked(
            test_db, PipelineStatus.COMPLETED)
        prun.started_at = datetime.now(UTC) - timedelta(seconds=90)
        prun.completed_at = datetime.now(UTC)
        test_db.commit()
        assert st._sync_open_schedule_runs(test_db) == 1
        test_db.expire_all()
        assert srun.status == "COMPLETED"
        assert srun.duration_seconds == 90
        assert sched.successful_runs == 1
        assert sched.last_run_status == "COMPLETED"
        # Re-sync is a no-op: counters bump exactly once.
        assert st._sync_open_schedule_runs(test_db) == 0
        assert sched.successful_runs == 1

    def test_failed_run_records_reason(self, test_db):
        import backend.tasks.schedule_tasks as st
        from backend.models import PipelineStatus
        sched, prun, srun = self._linked(
            test_db, PipelineStatus.FAILED, name="fail_pipe")
        prun.error_message = "boom"
        test_db.commit()
        assert st._sync_open_schedule_runs(test_db) == 1
        test_db.expire_all()
        assert srun.status == "FAILED"
        assert srun.error_message == "boom"
        assert sched.failed_runs == 1

    def test_missing_run_marked_failed(self, test_db):
        import backend.tasks.schedule_tasks as st
        from backend.models import ScheduleRun
        sched = _due_schedule(test_db, name="ghost_pipe")
        srun = ScheduleRun(
            schedule_id=sched.id,
            run_id=uuid_module.uuid4(),
            triggered_at=datetime.now(UTC),
            status="RUNNING",
        )
        test_db.add(srun)
        test_db.commit()
        assert st._sync_open_schedule_runs(test_db) == 1
        test_db.expire_all()
        assert srun.status == "FAILED"
        assert "missing" in (srun.error_message or "")

    def test_in_flight_left_open(self, test_db):
        import backend.tasks.schedule_tasks as st
        from backend.models import PipelineStatus
        _, _, srun = self._linked(
            test_db, PipelineStatus.RUNNING, name="run_pipe")
        assert st._sync_open_schedule_runs(test_db) == 0
        test_db.expire_all()
        assert srun.status == "RUNNING"
