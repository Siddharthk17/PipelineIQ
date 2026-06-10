import os
import uuid

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_E2E_TESTS") != "1",
    reason="Set RUN_E2E_TESTS=1 to run live HTTP API tests",
)

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000/api/v1")
AUTH_URL = os.getenv("E2E_AUTH_URL", "http://localhost:8000/auth")


@pytest.fixture(scope="module")
def viewer_token():
    email = f"viewer_{uuid.uuid4().hex[:8]}@test.com"
    username = f"viewer_{uuid.uuid4().hex[:8]}"
    password = "Str0ngP@ss123!"

    requests.post(
        f"{AUTH_URL}/register",
        json={"email": email, "username": username, "password": password},
    )

    login_resp = requests.post(
        f"{AUTH_URL}/login", json={"email": email, "password": password}
    )
    return login_resp.json()["access_token"]


def test_viewer_cannot_run_pipeline(viewer_token):
    headers = {"Authorization": f"Bearer {viewer_token}"}

    pipeline_yaml = """
pipeline:
  name: viewer_test
  steps:
    - name: load
      type: load
      file_id: some-uuid
    - name: save
      type: save
      input: load
      filename: output
"""
    resp = requests.post(
        f"{BASE_URL}/pipelines/run",
        headers=headers,
        json={"yaml_config": pipeline_yaml, "name": "Viewer Run"},
    )

    # Should be 403 Forbidden
    assert resp.status_code == 403
    assert "permission" in resp.text.lower() or "forbidden" in resp.text.lower()
