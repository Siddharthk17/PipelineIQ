"""Regression tests for M-132: expensive endpoints must be rate limited.

AI generate/repair burn Gemini quota and WASM validate burns compile CPU;
without per-IP rate limits one client can exhaust quota or stall workers.
Fix: slowapi limits on AI generate/repair and WASM validate/upload.
"""

from types import SimpleNamespace

from backend.tests.conftest import upload_file


class TestAIRateLimits:
    def test_generate_is_rate_limited(self, client, sales_csv_bytes, monkeypatch):
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")

        async def _fake_generate(**kwargs):
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

        statuses = []
        for _ in range(11):
            resp = client.post(
                "/api/ai/generate",
                json={
                    "description": "Build a simple ETL pipeline for the file",
                    "file_ids": [file_id],
                },
            )
            statuses.append(resp.status_code)
        assert 429 in statuses, f"expected a 429, got {statuses}"

    def test_repair_is_rate_limited(self, client, test_db, sales_csv_bytes, monkeypatch):
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

        statuses = []
        for _ in range(11):
            resp = client.post(f"/api/ai/runs/{run_id}/repair")
            statuses.append(resp.status_code)
        assert 429 in statuses, f"expected a 429, got {statuses}"


class TestWasmRateLimits:
    def test_validate_is_rate_limited(self, client):
        from wasmtime import wat2wasm

        tiny = bytes(wat2wasm('(module (func (export "f") (result f64) f64.const 1))'))
        statuses = []
        for _ in range(31):
            resp = client.post(
                "/api/v1/wasm/validate",
                files={"file": ("m.wasm", tiny, "application/wasm")},
            )
            statuses.append(resp.status_code)
            assert resp.status_code in (200, 429), resp.text
        assert 429 in statuses, f"expected a 429, got {statuses[-3:]}"
