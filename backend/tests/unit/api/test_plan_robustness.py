"""Regression tests for plan/preview robustness (FZ01).

Unparseable or non-mapping YAML must yield a client error, never a 500.
"""

import uuid


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"fz_{suffix}@t.local",
            "username": f"fz_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"fz_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_plan_with_scalar_yaml_returns_400_not_500(auth_client):
    headers = _register_login(auth_client)
    resp = auth_client.post(
        "/api/v1/pipelines/plan", json={"yaml_config": "0000000000"},
        headers=headers,
    )
    assert resp.status_code in (400, 422), resp.text


def test_plan_with_garbage_yaml_returns_400_not_500(auth_client):
    headers = _register_login(auth_client)
    resp = auth_client.post(
        "/api/v1/pipelines/plan",
        json={"yaml_config": "{{{not yaml"},
        headers=headers,
    )
    assert resp.status_code in (400, 422), resp.text


def test_plan_with_valid_yaml_still_works(
        auth_client, sales_csv_bytes):
    headers = _register_login(auth_client)
    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": ("s.csv", sales_csv_bytes, "text/csv")},
        headers=headers,
    )
    file_id = upload.json()["id"]
    yaml_config = (
        "pipeline:\n  name: fzplan\n  steps:\n"
        "    - name: src\n      type: load\n"
        f"      file_id: {file_id}\n"
    )
    resp = auth_client.post(
        "/api/v1/pipelines/plan", json={"yaml_config": yaml_config},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_steps"] == 1
