"""Regression test for R-R-005: a Redis outage must not silently stop
all schedule firing. The Redis distributed lock is best-effort; the
PostgreSQL row lock is the real single-flight guard, so a Redis
connection failure must fall through to the PG path (and say so),
not return a misleading ``locked: True`` with zero dispatches.
"""
import contextlib
from unittest.mock import MagicMock


def _due_schedule(test_db):
    import uuid as uuid_module
    from datetime import UTC, datetime, timedelta

    from backend.models import PipelineSchedule

    now = datetime.now(UTC)
    sched = PipelineSchedule(
        id=uuid_module.uuid4(),
        user_id=uuid_module.uuid4(),
        pipeline_name="redis_outage_pipe",
        yaml_config="pipeline:\n  name: redis_outage_pipe\n  steps: []\n",
        cron_expression="* * * * *",
        is_active=True,
        next_run_at=now - timedelta(minutes=5),
        total_runs=0,
        successful_runs=0,
        failed_runs=0,
        healed_runs=0,
    )
    test_db.add(sched)
    test_db.commit()
    return sched


def test_redis_outage_still_fires_due_schedules(test_db, monkeypatch):
    import backend.tasks.schedule_tasks as st
    from backend.db.redis_pools import get_cache_redis

    _due_schedule(test_db)
    dispatched = []
    fake_task = MagicMock()
    fake_task.apply_async.side_effect = lambda *a, **k: dispatched.append((a, k))
    monkeypatch.setattr(
        "backend.tasks.pipeline_tasks.execute_pipeline_task", fake_task)
    monkeypatch.setattr(st, "SessionLocal", lambda: test_db)

    def dead_redis():
        raise ConnectionError("redis is down")

    monkeypatch.setattr(st, "get_cache_redis", dead_redis)
    # Real (possibly stale) lock must not block the honest path.
    with contextlib.suppress(Exception):
        get_cache_redis().delete(st.SCHEDULE_CHECK_LOCK_KEY)

    result = st.check_schedules()
    assert result.get("locked") is not True, (
        "a Redis outage must not masquerade as another worker holding "
        "the lock")
    assert result["triggered"] == 1, (
        "due schedules must fire via the PG row-lock fallback when "
        "Redis is down")
    assert len(dispatched) == 1
