"""Regression tests for schedule atomicity + bounds (w1-daemon, M-028/M-071).

Uses the hermetic SQLite test DB and fakes; no Redis beat required.
"""


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
