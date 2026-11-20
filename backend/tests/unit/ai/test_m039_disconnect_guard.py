"""Regression test for M-039: AI generate/repair must not abort.

Bug: the endpoint raced the real generation task against a one-shot
``is_disconnected()`` check with FIRST_COMPLETED. The disconnect probe is
non-blocking, so it completes instantly while generation is still
running; the generation task was then cancelled and the endpoint blew up
with CancelledError instead of returning the result.
"""

import asyncio
from types import SimpleNamespace

from backend.tests.conftest import upload_file


def _slow_generation(**kwargs):
    async def _impl():
        await asyncio.sleep(0.5)
        return SimpleNamespace(
            yaml="pipeline:\n  name: ai_generated\n  steps: []\n",
            valid=True,
            attempts=1,
            error=None,
        )

    return _impl()


class TestDisconnectGuard:
    def test_slow_generation_returns_result_when_client_connected(
        self, client, sales_csv_bytes, monkeypatch
    ):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")

        async def _fake_generate(**kwargs):
            await asyncio.sleep(0.5)
            return SimpleNamespace(
                yaml="pipeline:\n  name: ai_generated\n  steps: []\n",
                valid=True,
                attempts=1,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.generate_pipeline_from_description",
            _fake_generate,
        )
        monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "test-key")

        response = client.post(
            "/api/ai/generate",
            json={
                "description": "Build a simple ETL pipeline for the uploaded sales file",
                "file_ids": [file_id],
            },
        )

        assert response.status_code == 200
        assert response.json()["valid"] is True

    def test_slow_repair_returns_result_when_client_connected(
        self, client, test_db, sales_csv_bytes, monkeypatch
    ):
        from backend.models import PipelineRun, PipelineStatus
        from backend.tests.conftest import build_simple_pipeline_yaml
        from backend.utils.uuid_utils import as_uuid

        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)
        run_id = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config},
        ).json()["run_id"]

        run = test_db.query(PipelineRun).filter(PipelineRun.id == as_uuid(run_id)).first()
        run.status = PipelineStatus.FAILED
        run.error_message = "Column 'ammount' not found"
        test_db.commit()

        async def _fake_repair(**kwargs):
            await asyncio.sleep(0.5)
            return SimpleNamespace(
                corrected_yaml="pipeline:\n  name: repaired\n  steps: []\n",
                diff_lines=[],
                valid=True,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.repair_pipeline_from_error",
            _fake_repair,
        )
        monkeypatch.setattr("backend.config.settings.GEMINI_API_KEY", "test-key")

        response = client.post(f"/api/ai/runs/{run_id}/repair")

        assert response.status_code == 200
        assert response.json()["valid"] is True
