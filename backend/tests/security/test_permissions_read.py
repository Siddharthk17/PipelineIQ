"""Read-path permission tests exercised with real authentication (M-051).

Replaces the permanently-skipped live-HTTP suite
(``backend/tests/unit/security/test_permissions_read.py``) with
equivalent tests running against ``TestClient`` + real JWTs. Assertions
mirror the original suite.
"""

import uuid

PASSWORD = "Testpass123!"


def _register(auth_client, email, username):
    resp = auth_client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text


def _login(auth_client, email):
    resp = auth_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _upload(auth_client, headers, name="data.csv"):
    resp = auth_client.post(
        "/api/v1/files/upload",
        files={"file": (name, b"id,val\n1,10\n", "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _pipeline_yaml(name, file_id):
    return (
        f"pipeline:\n  name: {name}\n  steps:\n"
        f"    - name: load\n      type: load\n      file_id: {file_id}\n"
        f"    - name: save\n      type: save\n      input: load\n      filename: out"
    )


def test_read_export_cancel_require_ownership(auth_client):
    """A viewer must not read/export/cancel another user's run; the owner
    can read their own run — mirroring skipped test_read_permissions."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"read_admin_{tag}@t.local"
    viewer_email = f"read_viewer_{tag}@t.local"
    _register(auth_client, admin_email, f"read_admin_{tag}")
    _register(auth_client, viewer_email, f"read_viewer_{tag}")
    admin_headers = _login(auth_client, admin_email)
    viewer_headers = _login(auth_client, viewer_email)

    file_id = _upload(auth_client, admin_headers, "read.csv")
    name = f"read_test_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(name, file_id), "name": name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    # Owner can read their own run.
    resp = auth_client.get(f"/api/v1/pipelines/{run_id}", headers=admin_headers)
    assert resp.status_code == 200, resp.text

    # Viewer must be denied on read ...
    resp = auth_client.get(f"/api/v1/pipelines/{run_id}", headers=viewer_headers)
    assert resp.status_code == 404, resp.text

    # ... on export ...
    resp = auth_client.get(f"/api/v1/pipelines/{run_id}/export", headers=viewer_headers)
    assert resp.status_code == 404, resp.text

    # ... and on cancel (separate run so state is fresh).
    cancel_name = f"cancel_test_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(cancel_name, file_id), "name": cancel_name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    cancel_run_id = resp.json()["run_id"]
    resp = auth_client.post(f"/api/v1/pipelines/{cancel_run_id}/cancel", headers=viewer_headers)
    assert resp.status_code == 404, resp.text


def test_unauthenticated_read_access_denied(auth_client):
    """No token at all must not read a run."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"naread_admin_{tag}@t.local"
    _register(auth_client, admin_email, f"naread_admin_{tag}")
    admin_headers = _login(auth_client, admin_email)
    file_id = _upload(auth_client, admin_headers, "naread.csv")
    name = f"naread_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(name, file_id), "name": name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    # Drop the login cookie the TestClient jar kept: this request must carry
    # no credentials at all.
    auth_client.cookies.clear()
    resp = auth_client.get(f"/api/v1/pipelines/{run_id}")
    assert resp.status_code in (401, 403), resp.text
