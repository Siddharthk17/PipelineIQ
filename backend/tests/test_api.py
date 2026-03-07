"""Integration tests for PipelineIQ API endpoints.

Uses FastAPI TestClient with an in-memory SQLite database.
Celery task dispatch is mocked to avoid needing a running worker.
"""

import time

import pytest
from fastapi.testclient import TestClient

from backend.tests.conftest import build_simple_pipeline_yaml, upload_file



class TestHealthEndpoint:
    """Tests for the /health endpoint."""

    def test_health_endpoint_returns_200(self, client):
        """Health check returns 200."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ("ok", "degraded")



class TestFileUpload:
    """Tests for file upload endpoints."""

    def test_upload_valid_csv_returns_201_with_metadata(self, client, sales_csv_bytes):
        """Valid CSV upload returns 201 with file metadata."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["row_count"] > 0
        assert len(data["columns"]) > 0

    def test_upload_valid_json_returns_201(self, client, sample_json_bytes):
        """Valid JSON upload returns 201."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("data.json", sample_json_bytes, "application/json")},
        )
        assert response.status_code == 201

    def test_upload_invalid_extension_returns_400(self, client):
        """Uploading .exe returns 400 with extension error."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("malware.exe", b"MZ\x90", "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "extension" in response.json()["detail"].lower()

    def test_upload_empty_file_returns_400(self, client):
        """Uploading empty file returns 400."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("empty.csv", b"", "text/csv")},
        )
        assert response.status_code == 400

    def test_upload_csv_with_path_traversal_filename_is_sanitized(self, client):
        """Path traversal in filename is sanitized."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("../../etc/passwd.csv", b"col1\nval1", "text/csv")},
        )
        if response.status_code == 201:
            data = response.json()
            assert ".." not in data.get("original_filename", "")


class TestFileGet:
    """Tests for file retrieval endpoints."""

    def test_get_file_returns_correct_metadata(self, client, sales_csv_bytes):
        """GET /files/{id} returns correct metadata."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        response = client.get(f"/api/v1/files/{file_id}")
        assert response.status_code == 200
        assert response.json()["id"] == file_id

    def test_get_nonexistent_file_returns_404(self, client):
        """GET /files/{id} with nonexistent UUID returns 404."""
        response = client.get("/api/v1/files/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_get_file_with_invalid_uuid_returns_422(self, client):
        """GET /files/{id} with invalid UUID returns 422."""
        response = client.get("/api/v1/files/not-a-uuid")
        assert response.status_code == 422


class TestFileDelete:
    """Tests for file deletion endpoints."""

    def test_delete_file_removes_from_db(self, client, sales_csv_bytes):
        """DELETE /files/{id} removes from DB."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        response = client.delete(f"/api/v1/files/{file_id}")
        assert response.status_code == 200
        assert client.get(f"/api/v1/files/{file_id}").status_code == 404

    def test_delete_nonexistent_file_returns_404(self, client):
        """DELETE /files/{id} with nonexistent UUID returns 404."""
        response = client.delete("/api/v1/files/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404



class TestPipelineValidation:
    """Tests for pipeline validation endpoints."""

    def test_validate_valid_pipeline_returns_is_valid_true(self, client, sales_csv_bytes):
        """Valid pipeline YAML returns is_valid=True."""
        file_id = upload_file(client, sales_csv_bytes)
        yaml_config = build_simple_pipeline_yaml(file_id)
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        assert response.json()["is_valid"] is True
        assert response.json()["errors"] == []

    def test_validate_yaml_with_unknown_file_id_returns_error(self, client):
        """Pipeline with unknown file_id returns is_valid=False."""
        yaml_config = build_simple_pipeline_yaml("00000000-0000-0000-0000-000000000000")
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": yaml_config},
        )
        assert response.json()["is_valid"] is False
        assert len(response.json()["errors"]) > 0

    def test_validate_malformed_yaml_returns_422(self, client):
        """Malformed YAML returns 422 (Pydantic validator catches it)."""
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": "{{{{not: valid: yaml"},
        )
        assert response.status_code == 422

    def test_validate_yaml_bomb_does_not_hang(self, client):
        """yaml.safe_load handles billion laughs safely."""
        yaml_bomb = """
a: &a ["lol","lol","lol","lol","lol","lol","lol","lol","lol"]
b: &b [*a,*a,*a,*a,*a,*a,*a,*a,*a]
c: &c [*b,*b,*b,*b,*b,*b,*b,*b,*b]
pipeline:
  name: bomb
  steps: []
"""
        start = time.time()
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": yaml_bomb},
        )
        duration = time.time() - start
        assert duration < 2.0

    def test_validate_empty_yaml_returns_422(self, client):
        """Empty YAML string returns 422 (Pydantic min_length=10)."""
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": ""},
        )
        assert response.status_code == 422

    def test_validate_extremely_long_yaml_returns_response(self, client):
        """50,000-char YAML is handled without crashing."""
        long_yaml = "pipeline:\n  name: test\n  steps:\n" + (
            "    - name: s\n      type: load\n" * 1000
        )
        response = client.post(
            "/api/v1/pipelines/validate",
            json={"yaml_config": long_yaml},
        )
        assert response.status_code in [200, 400, 422]



class TestPipelineExecution:
    """Tests for pipeline execution endpoints."""

    def test_run_pipeline_returns_immediately_with_run_id(self, client, sales_csv_bytes):
        """Pipeline run returns immediately with run_id (async dispatch)."""
        file_id = upload_file(client, sales_csv_bytes)
        yaml_config = build_simple_pipeline_yaml(file_id)
        start = time.time()
        response = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config},
        )
        duration = time.time() - start
        assert response.status_code == 202
        assert "run_id" in response.json()
        assert duration < 1.0

    def test_run_pipeline_creates_db_record_with_pending_status(self, client, sales_csv_bytes):
        """Pipeline run creates a DB record with PENDING status."""
        file_id = upload_file(client, sales_csv_bytes)
        yaml_config = build_simple_pipeline_yaml(file_id)
        run_id = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config},
        ).json()["run_id"]
        run = client.get(f"/api/v1/pipelines/{run_id}").json()
        assert run["status"] in ["PENDING", "RUNNING"]

    def test_get_nonexistent_pipeline_returns_404(self, client):
        """GET /pipelines/{id} with nonexistent UUID returns 404."""
        response = client.get("/api/v1/pipelines/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_list_pipelines_returns_paginated_results(self, client):
        """GET /pipelines/ returns list response."""
        response = client.get("/api/v1/pipelines/")
        assert response.status_code == 200
        data = response.json()
        assert "runs" in data
        assert "total" in data

    def test_list_pipelines_empty_returns_empty_list(self, client):
        """GET /pipelines/ on empty DB returns empty list."""
        response = client.get("/api/v1/pipelines/")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["runs"] == []



class TestLineageEndpoints:
    """Tests for lineage query endpoints."""

    def test_get_lineage_for_nonexistent_run_returns_404(self, client):
        """GET /lineage/{id} with nonexistent run returns 404."""
        response = client.get("/api/v1/lineage/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404

    def test_get_lineage_with_invalid_uuid_returns_422(self, client):
        """GET /lineage/{id} with invalid UUID returns 422."""
        response = client.get("/api/v1/lineage/not-a-uuid")
        assert response.status_code == 422


class TestPlanEndpoint:
    """Tests for the /pipelines/plan endpoint."""

    def test_plan_returns_step_estimates(self, client, sales_csv_bytes):
        """Plan endpoint returns step-level estimates."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)
        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": yaml_config},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total_steps"] == 3
        assert len(data["steps"]) == 3
        assert data["will_succeed"] is True

    def test_plan_includes_files_read(self, client, sales_csv_bytes):
        """Plan includes file IDs that will be read."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)
        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": yaml_config},
        )
        data = response.json()
        assert file_id in data["files_read"]

    def test_plan_includes_files_written(self, client, sales_csv_bytes):
        """Plan includes output filenames."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        yaml_config = build_simple_pipeline_yaml(file_id)
        response = client.post(
            "/api/v1/pipelines/plan",
            json={"yaml_config": yaml_config},
        )
        data = response.json()
        assert "output.csv" in data["files_written"]


class TestVersionsEndpoint:
    """Tests for version API endpoints."""

    def test_list_versions_empty(self, client):
        """List versions returns empty for unknown pipeline."""
        response = client.get("/api/v1/versions/unknown_pipeline")
        assert response.status_code == 200
        assert response.json()["total_versions"] == 0

    def test_get_nonexistent_version_returns_404(self, client):
        """Getting a nonexistent version returns 404."""
        response = client.get("/api/v1/versions/my_pipeline/999")
        assert response.status_code == 404

    def test_restore_nonexistent_version_returns_404(self, client):
        """Restoring a nonexistent version returns 404."""
        response = client.post("/api/v1/versions/my_pipeline/restore/999")
        assert response.status_code == 404

    def test_diff_nonexistent_versions_returns_404(self, client):
        """Diffing nonexistent versions returns 404."""
        response = client.get("/api/v1/versions/my_pipeline/diff/1/2")
        assert response.status_code == 404


class TestSchemaEndpoints:
    """Tests for schema drift endpoints."""

    def test_schema_history_after_upload(self, client, sales_csv_bytes):
        """Schema history is populated after file upload."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        response = client.get(f"/api/v1/files/{file_id}/schema/history")
        assert response.status_code == 200
        assert response.json()["total_snapshots"] >= 1

    def test_schema_diff_no_drift_single_upload(self, client, sales_csv_bytes):
        """Schema diff with single upload shows no drift."""
        file_id = upload_file(client, sales_csv_bytes, "sales.csv")
        response = client.get(f"/api/v1/files/{file_id}/schema/diff")
        assert response.status_code == 200
        assert response.json()["has_drift"] is False

    def test_schema_history_nonexistent_file_returns_404(self, client):
        """Schema history for nonexistent file returns 404."""
        response = client.get("/api/v1/files/00000000-0000-0000-0000-000000000000/schema/history")
        assert response.status_code == 404
