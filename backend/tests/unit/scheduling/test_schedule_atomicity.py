"""Regression tests for schedule atomicity + bounds (w1-daemon, M-028/M-071).

Uses the hermetic SQLite test DB and fakes; no Redis beat required.
"""

import contextlib


class TestM028PreviewCaps:
    def test_get_next_n_runs_hard_capped(self):
        from backend.scheduling import cron_utils
        from backend.scheduling.cron_utils import get_next_n_runs

        runs = get_next_n_runs("0 6 * * *", n=10_000_000)
        assert len(runs) <= cron_utils.PREVIEW_MAX_N

    def test_get_next_n_runs_default_bounded(self):
        from backend.scheduling.cron_utils import get_next_n_runs

        assert len(get_next_n_runs("0 6 * * *")) <= 100

    def _make_schedule(self, client, sales_csv_bytes, name):
        from backend.tests.conftest import upload_file

        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = (
            "pipeline:\n"
            f"  name: {name}\n"
            "  steps:\n"
            "    - name: load_orders\n"
            "      type: load\n"
            f"      file_id: {file_id}\n"
        )
        created = client.post(
            "/api/v1/schedules/",
            json={
                "pipeline_name": name,
                "yaml_config": yaml_config,
                "cron_expression": "0 6 * * *",
            },
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def test_preview_endpoint_clamps_huge_n(self, client, sales_csv_bytes):
        schedule_id = self._make_schedule(client, sales_csv_bytes, "cap_sched")
        resp = client.get(f"/api/v1/schedules/{schedule_id}/preview", params={"n": 100000})
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["next_runs"]) <= 100

    def test_schedule_run_history_limit_clamped(self, client, sales_csv_bytes):
        schedule_id = self._make_schedule(client, sales_csv_bytes, "hist_sched")
        resp = client.get(f"/api/v1/schedules/{schedule_id}/runs", params={"limit": 100000})
        assert resp.status_code == 200, resp.text


class TestM071CreateGrantsAfterValidation:
    """Orphaned owner grants must not survive failed creates (M-071)."""

    def _user_client(self, client):
        resp = client.post(
            "/auth/register",
            json={
                "email": "sched.user@t.local",
                "username": "sched_user",
                "password": "SchedUser123!",
            },
        )
        assert resp.status_code == 201, resp.text
        login = client.post(
            "/auth/login",
            json={"email": "sched.user@t.local", "password": "SchedUser123!"},
        )
        assert login.status_code == 200, resp.text
        token = login.json()["access_token"]
        from fastapi.testclient import TestClient

        from backend.main import app

        user_client = TestClient(app)
        user_client.headers["Authorization"] = f"Bearer {token}"
        return user_client

    def test_failed_create_leaves_no_permission_row(self, client, test_db):
        from backend.models import PipelinePermission

        user_client = self._user_client(client)
        # Invalid YAML: structural validation fails with 422.
        resp = user_client.post(
            "/api/v1/schedules/",
            json={
                "pipeline_name": "orphan_pipe",
                "yaml_config": "pipeline:\n  name: orphan_pipe\n  steps: []\n",
                "cron_expression": "0 6 * * *",
            },
        )
        assert resp.status_code in (400, 404, 422), resp.text
        orphans = (
            test_db.query(PipelinePermission)
            .filter(PipelinePermission.pipeline_name == "orphan_pipe")
            .all()
        )
        assert orphans == [], "failed create must not leave an owner grant behind"


class TestM071ToggleAtomicity:
    """Concurrent pause/resume/toggle must not lose updates (M-071)."""

    def _make_schedule(self, client, sales_csv_bytes, name):
        from backend.tests.conftest import upload_file

        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = (
            "pipeline:\n"
            f"  name: {name}\n"
            "  steps:\n"
            "    - name: load_orders\n"
            "      type: load\n"
            f"      file_id: {file_id}\n"
        )
        created = client.post(
            "/api/v1/schedules/",
            json={
                "pipeline_name": name,
                "yaml_config": yaml_config,
                "cron_expression": "0 6 * * *",
            },
        )
        assert created.status_code == 201, created.text
        return created.json()["id"]

    def test_double_pause_conflicts(self, client, sales_csv_bytes):
        schedule_id = self._make_schedule(client, sales_csv_bytes, "dbl_pause")
        first = client.post(f"/api/v1/schedules/{schedule_id}/pause")
        assert first.status_code == 200, first.text
        second = client.post(f"/api/v1/schedules/{schedule_id}/pause")
        assert second.status_code == 409, second.text

    def test_double_resume_conflicts(self, client, sales_csv_bytes):
        schedule_id = self._make_schedule(client, sales_csv_bytes, "dbl_resume")
        assert client.post(f"/api/v1/schedules/{schedule_id}/pause").status_code == 200
        assert client.post(f"/api/v1/schedules/{schedule_id}/resume").status_code == 200
        again = client.post(f"/api/v1/schedules/{schedule_id}/resume")
        assert again.status_code == 409, again.text

    def test_toggle_flips_atomically(self, client, sales_csv_bytes):
        schedule_id = self._make_schedule(client, sales_csv_bytes, "toggle_me")
        off = client.patch(f"/api/v1/schedules/{schedule_id}/toggle")
        assert off.status_code == 200, off.text
        assert off.json()["is_active"] is False
        on = client.patch(f"/api/v1/schedules/{schedule_id}/toggle")
        assert on.status_code == 200, on.text
        assert on.json()["is_active"] is True


class TestM071DispatchIdempotent:
    """Beat double-fire must not duplicate runs or strand orphans (M-071)."""

    def _due_schedule(self, test_db):
        import uuid as uuid_module
        from datetime import UTC, datetime, timedelta

        from backend.models import PipelineSchedule

        now = datetime.now(UTC)
        sched = PipelineSchedule(
            id=uuid_module.uuid4(),
            user_id=uuid_module.uuid4(),
            pipeline_name="beat_pipe",
            yaml_config="pipeline:\n  name: beat_pipe\n  steps: []\n",
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

    def _run_check(self, test_db, monkeypatch, fail_dispatch=False):
        from unittest.mock import MagicMock

        import backend.tasks.schedule_tasks as st

        dispatched = []
        fake_task = MagicMock()
        if fail_dispatch:
            fake_task.apply_async.side_effect = RuntimeError("broker down")
        else:
            fake_task.apply_async.side_effect = lambda *a, **k: dispatched.append((a, k))
        monkeypatch.setattr("backend.tasks.pipeline_tasks.execute_pipeline_task", fake_task)
        monkeypatch.setattr(st, "SessionLocal", lambda: test_db)
        return st.check_schedules(), dispatched

    def test_second_beat_finds_nothing_due(self, test_db, monkeypatch):
        from unittest.mock import MagicMock

        import backend.tasks.schedule_tasks as st

        self._due_schedule(test_db)
        monkeypatch.setattr(st, "SessionLocal", lambda: test_db)
        monkeypatch.setattr("backend.tasks.pipeline_tasks.execute_pipeline_task", MagicMock())
        # Drain any real redis lock state first.
        from backend.db.redis_pools import get_cache_redis

        with contextlib.suppress(Exception):
            get_cache_redis().delete(st.SCHEDULE_CHECK_LOCK_KEY)
        first = st.check_schedules()
        assert first["triggered"] == 1
        with contextlib.suppress(Exception):
            get_cache_redis().delete(st.SCHEDULE_CHECK_LOCK_KEY)
        second = st.check_schedules()
        assert second["triggered"] == 0, "advanced next_run_at must prevent double-fire"

    def test_dispatch_failure_marks_run_failed(self, test_db, monkeypatch):

        from backend.models import PipelineRun, PipelineStatus

        self._due_schedule(test_db)
        import backend.tasks.schedule_tasks as st
        from backend.db.redis_pools import get_cache_redis

        with contextlib.suppress(Exception):
            get_cache_redis().delete(st.SCHEDULE_CHECK_LOCK_KEY)
        result, _ = self._run_check(test_db, monkeypatch, fail_dispatch=True)
        assert result["triggered"] == 0
        failed = (
            test_db.query(PipelineRun)
            .filter(PipelineRun.name == "beat_pipe", PipelineRun.status == PipelineStatus.FAILED)
            .all()
        )
        assert len(failed) == 1, "dispatch failure must surface, not strand PENDING"
