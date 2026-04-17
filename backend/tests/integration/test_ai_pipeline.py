"""Integration checks for AI pipeline endpoints.

These tests are gated behind RUN_INTEGRATION_TESTS=1 and avoid live Gemini calls
by monkeypatching generation helpers.
"""

from types import SimpleNamespace
import os

import pytest

from backend.models import PipelineRun, PipelineStatus
from backend.tests.conftest import build_simple_pipeline_yaml, upload_file
from backend.utils.uuid_utils import as_uuid


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Set RUN_INTEGRATION_TESTS=1 to run AI integration checks",
)


class TestAIPipelineIntegration:
    def test_generate_then_validate_yaml(self, client, sales_csv_bytes, monkeypatch):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")

        generated_yaml = f"""
pipeline:
  name: ai_generated_pipeline
  steps:
    - name: load_data
      type: load
      file_id: {file_id}
    - name: save_data
      type: save
      input: load_data
      filename: ai_output.csv
""".strip()

        async def _fake_generate_pipeline_from_description(**kwargs):
            assert kwargs["file_ids"] == [file_id]
            return SimpleNamespace(
                yaml=generated_yaml,
                valid=True,
                attempts=1,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.generate_pipeline_from_description",
            _fake_generate_pipeline_from_description,
        )

        generate_response = client.post(
            "/api/ai/generate",
            json={
                "description": "Build a simple load and save pipeline for my uploaded file",
                "file_ids": [file_id],
            },
        )
        assert generate_response.status_code == 200
        assert generate_response.json()["valid"] is True

        validate_response = client.post(
            "/api/ai/validate-yaml",
            json={"yaml_text": generate_response.json()["yaml"]},
        )
        assert validate_response.status_code == 200
        assert validate_response.json()["valid"] is True
        assert validate_response.json()["step_count"] == 2

    def test_repair_failed_run_returns_diff(self, client, test_db, sales_csv_bytes, monkeypatch):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)
        run_id = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config},
        ).json()["run_id"]

        run = test_db.query(PipelineRun).filter(PipelineRun.id == as_uuid(run_id)).first()
        assert run is not None
        run.status = PipelineStatus.FAILED
        run.error_message = "Column 'ammount' not found"
        test_db.commit()

        async def _fake_repair_pipeline_from_error(**kwargs):
            assert kwargs["failed_step"]
            return SimpleNamespace(
                corrected_yaml=yaml_config.replace("ammount", "amount"),
                diff_lines=[{"type": "removed", "content": "ammount"}, {"type": "added", "content": "amount"}],
                valid=True,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.repair_pipeline_from_error",
            _fake_repair_pipeline_from_error,
        )

        response = client.post(f"/api/ai/runs/{run_id}/repair")
        assert response.status_code == 200
        payload = response.json()
        assert payload["valid"] is True
        assert payload["diff_lines"]

