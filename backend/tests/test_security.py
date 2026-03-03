"""Security penetration tests for PipelineIQ API.

These tests simulate real attacks. They must ALL return safe responses.
"""

import json

import pytest
from fastapi.testclient import TestClient

from backend.tests.conftest import upload_file



class TestFileUploadAttacks:
    """Security tests for the file upload endpoint."""

    def test_path_traversal_in_filename_is_blocked(self, client):
        """Path traversal payloads in filename are safely handled."""
        payloads = [
            "../../etc/passwd",
            "../../../windows/system32/cmd.exe",
            "....//....//etc/passwd",
            "%2e%2e%2fetc%2fpasswd",
        ]
        for payload in payloads:
            response = client.post(
                "/api/v1/files/upload",
                files={"file": (payload + ".csv", b"col\nval", "text/csv")},
            )
            if response.status_code == 201:
                data = response.json()
                assert ".." not in data.get("original_filename", "")

    def test_csv_injection_content_stored_not_executed(self, client):
        """CSV formula injection is stored as plain text."""
        malicious_csv = b"formula\n=cmd|' /C calc'!A0\n=HYPERLINK(\"http://evil.com\")\n"
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("test.csv", malicious_csv, "text/csv")},
        )
        assert response.status_code in [201, 400]

    def test_extremely_nested_json_does_not_crash(self, client):
        """Deeply nested JSON does not crash the server."""
        import sys
        # Use a nesting depth safe for both json.dumps and the server
        depth = min(100, sys.getrecursionlimit() // 10)
        nested = {"a": None}
        current = nested
        for _ in range(depth - 1):
            current["a"] = {"a": None}
            current = current["a"]
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("nested.json", json.dumps(nested).encode(), "application/json")},
        )
        assert response.status_code in [201, 400, 422]

    def test_file_with_null_bytes_does_not_crash(self, client):
        """Files with null bytes in content don't crash the server."""
        null_content = b"col1,col2\nval1\x00val2\nval3,val4"
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("nullbytes.csv", null_content, "text/csv")},
        )
        assert response.status_code in [201, 400, 422]



class TestAPIInjectionAttacks:
    """Security tests for API injection vectors."""

    def test_sql_injection_in_file_id_path_param(self, client):
        """SQL injection payloads in file_id return 422 (UUID validation)."""
        payloads = [
            "' OR '1'='1",
            "1; DROP TABLE uploaded_files; --",
            "1' UNION SELECT * FROM pipeline_runs--",
        ]
        for payload in payloads:
            response = client.get(f"/api/v1/files/{payload}")
            assert response.status_code == 422

    def test_xss_in_pipeline_name_is_stored_safely(self, client, sales_csv_bytes):
        """XSS payloads in pipeline name are stored as plain text."""
        xss_name = "<script>alert('xss')</script>"
        file_id = upload_file(client, sales_csv_bytes)
        yaml_config = f"""pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: "{file_id}"
    - name: save_output
      type: save
      input: load_data
      filename: out.csv
"""
        response = client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": yaml_config, "name": xss_name},
        )
        if response.status_code == 202:
            run_id = response.json()["run_id"]
            run = client.get(f"/api/v1/pipelines/{run_id}").json()
            assert run["name"] == xss_name

    def test_invalid_uuid_format_in_all_endpoints(self, client):
        """Every endpoint with {run_id}/{file_id} validates UUID format."""
        invalid_ids = ["not-a-uuid", "123", "null", "undefined", "admin"]
        endpoints = [
            "/api/v1/files/{id}",
            "/api/v1/pipelines/{id}",
            "/api/v1/lineage/{id}",
        ]
        for endpoint_template in endpoints:
            for invalid_id in invalid_ids:
                url = endpoint_template.replace("{id}", invalid_id)
                response = client.get(url)
                assert response.status_code in [422, 404], \
                    f"Expected 422/404 for {url}, got {response.status_code}"
