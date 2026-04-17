"""Tests for AI API endpoints."""

from types import SimpleNamespace

from backend.models import PipelineRun, PipelineStatus
from backend.tests.conftest import build_simple_pipeline_yaml, upload_file
from backend.utils.uuid_utils import as_uuid


class TestAIEndpoints:
    def test_generate_pipeline_endpoint_returns_generated_yaml(
        self, client, sales_csv_bytes, monkeypatch
    ):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")

        async def _fake_generate_pipeline_from_description(**kwargs):
            assert kwargs["description"].startswith("Build")
            assert kwargs["file_ids"] == [file_id]
            return SimpleNamespace(
                yaml="pipeline:\n  name: ai_generated\n  steps: []\n",
                valid=True,
                attempts=1,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.generate_pipeline_from_description",
            _fake_generate_pipeline_from_description,
        )

        response = client.post(
            "/api/ai/generate",
            json={
                "description": "Build a simple ETL pipeline for the uploaded sales file",
                "file_ids": [file_id],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["attempts"] == 1
        assert "pipeline:" in data["yaml"]

    def test_repair_failed_run_returns_corrected_yaml(
        self, client, test_db, sales_csv_bytes, monkeypatch
    ):
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
            assert kwargs["failed_step"] in {"unknown", "load_data", "filter_data"}
            return SimpleNamespace(
                corrected_yaml="pipeline:\n  name: repaired\n  steps: []\n",
                diff_lines=[{"type": "added", "content": "  name: repaired"}],
                valid=True,
                error=None,
            )

        monkeypatch.setattr(
            "backend.routers.ai.repair_pipeline_from_error",
            _fake_repair_pipeline_from_error,
        )

        response = client.post(f"/api/ai/runs/{run_id}/repair")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert "corrected_yaml" in data
        assert data["diff_lines"][0]["type"] == "added"

    def test_autocomplete_columns_batch_returns_suggestions(self, client):
        response = client.post(
            "/api/ai/autocomplete/columns",
            json={
                "typed_columns": ["ammount", "regoin"],
                "available_columns": ["amount", "region", "status"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data
        assert data["suggestions"]["ammount"] == "amount"
        assert data["suggestions"]["regoin"] == "region"

    def test_validate_yaml_endpoint_returns_step_count(self, client):
        response = client.post(
            "/api/ai/validate-yaml",
            json={
                "yaml_text": (
                    "pipeline:\n"
                    "  name: test_validate\n"
                    "  steps:\n"
                    "    - name: load_data\n"
                    "      type: load\n"
                    "      file_id: 00000000-0000-0000-0000-000000000000\n"
                )
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["step_count"] == 1
