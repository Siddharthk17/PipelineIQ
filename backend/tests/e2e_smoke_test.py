import requests
import uuid
import time
import pytest
import os
from pathlib import Path

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"
FILE_PATH = "sample_data/sales.csv"


class TestE2ESmoke:
    def setup_method(self):
        # Use demo admin user for E2E test (has admin role, can run any pipeline)
        self.user_email = os.environ.get("TEST_ADMIN_EMAIL", "demo@pipelineiq.app")
        self.password = os.environ.get("TEST_ADMIN_PASSWORD", "Demo1234!")
        self.token = None

    def test_full_pipeline_lifecycle(self):
        # 1. Login as demo admin user (has admin role, can run any pipeline)
        login_resp = requests.post(
            f"{AUTH_URL}/login",
            json={"email": self.user_email, "password": self.password},
        )
        assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"
        self.token = login_resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {self.token}"}

        # 2. Upload File
        with open(FILE_PATH, "rb") as f:
            upload_resp = requests.post(
                f"{BASE_URL}/files/upload",
                headers=headers,
                files={"file": (FILE_PATH, f, "text/csv")},
            )
        assert upload_resp.status_code == 201, f"Upload failed: {upload_resp.text}"
        file_id = upload_resp.json()["id"]

        # 3. Validate Pipeline
        pipeline_yaml = f"""
pipeline:
  name: smoke_test_pipeline
  steps:
    - name: load_data
      type: load
      file_id: {file_id}
    - name: delivered_only
      type: filter
      input: load_data
      column: status
      operator: equals
      value: delivered
    - name: save_result
      type: save
      input: delivered_only
      filename: smoke_output
"""
        val_resp = requests.post(
            f"{BASE_URL}/pipelines/validate",
            headers=headers,
            json={"yaml_config": pipeline_yaml},
        )
        assert val_resp.status_code == 200
        assert val_resp.json()["is_valid"] is True

        # 4. Run Pipeline
        run_resp = requests.post(
            f"{BASE_URL}/pipelines/run",
            headers=headers,
            json={"yaml_config": pipeline_yaml, "name": "Smoke Run"},
        )
        assert run_resp.status_code == 202
        run_id = run_resp.json()["run_id"]

        # 5. Poll for completion
        max_retries = 20
        completed = False
        for _ in range(max_retries):
            status_resp = requests.get(
                f"{BASE_URL}/pipelines/{run_id}", headers=headers
            )
            assert status_resp.status_code == 200
            if status_resp.json()["status"] == "COMPLETED":
                completed = True
                break
            elif status_resp.json()["status"] == "FAILED":
                pytest.fail(
                    f"Pipeline failed: {status_resp.json().get('error_message')}"
                )
            time.sleep(2)

        assert completed, "Pipeline timed out"

        # 6. Verify Export
        export_resp = requests.get(
            f"{BASE_URL}/pipelines/{run_id}/export", headers=headers
        )
        assert export_resp.status_code == 200
        assert "text/csv" in export_resp.headers.get("Content-Type", "")

        # 7. Verify Lineage
        lineage_resp = requests.get(f"{BASE_URL}/lineage/{run_id}", headers=headers)
        assert lineage_resp.status_code == 200
        data = lineage_resp.json()
        assert "nodes" in data
        assert "edges" in data
        # Verify source file node exists
        assert any(node["type"] == "sourceFile" for node in data["nodes"])
