"""Cross-user access (security leak) tests with real authentication (M-051).

Replaces the permanently-skipped live-HTTP suite
(``backend/tests/unit/security/test_security_leak.py``) with equivalent
tests running against ``TestClient`` + real JWTs. Assertions mirror the
original suite.
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
    body = resp.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]


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


def test_cross_user_run_access_denied(auth_client):
    """Another user must not access a run they do not own — mirroring
    skipped test_leak: 401/403/404, never the run payload."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"leak_admin_{tag}@t.local"
    viewer_email = f"leaker_{tag}@t.local"
    _register(auth_client, admin_email, f"leak_admin_{tag}")
    _register(auth_client, viewer_email, f"leaker_{tag}")
    admin_headers, _ = _login(auth_client, admin_email)
    viewer_headers, _ = _login(auth_client, viewer_email)

    file_id = _upload(auth_client, admin_headers, "leak.csv")
    name = f"leak_test_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(name, file_id), "name": name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    resp = auth_client.get(f"/api/v1/pipelines/{run_id}", headers=viewer_headers)
    assert resp.status_code in (401, 403, 404), resp.text
    # The denial must not leak the run payload.
    assert run_id not in resp.text


def test_cross_user_cannot_enumerate_runs(auth_client):
    """Another user's run must not appear in a non-owner's run listing."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"enum_admin_{tag}@t.local"
    viewer_email = f"enum_viewer_{tag}@t.local"
    _register(auth_client, admin_email, f"enum_admin_{tag}")
    _register(auth_client, viewer_email, f"enum_viewer_{tag}")
    admin_headers, _ = _login(auth_client, admin_email)
    viewer_headers, _ = _login(auth_client, viewer_email)

    file_id = _upload(auth_client, admin_headers, "enum.csv")
    name = f"enum_secret_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(name, file_id), "name": name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]

    resp = auth_client.get("/api/v1/pipelines/", headers=viewer_headers)
    assert resp.status_code == 200, resp.text
    assert run_id not in resp.text
