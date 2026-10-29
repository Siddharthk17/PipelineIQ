"""Malformed run IDs on run-scoped endpoints must not 500 (S20.6)."""

import uuid


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"ru_{suffix}@t.local",
            "username": f"ru_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"ru_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_runs_timing_garbage_id(auth_client):
    headers = _register_login(auth_client)
    resp = auth_client.get("/api/runs/not-a-uuid/timing", headers=headers)
    assert resp.status_code in (404, 422), resp.text


def test_runs_timing_missing_id_404s(auth_client):
    headers = _register_login(auth_client)
    missing = str(uuid.uuid4())
    resp = auth_client.get(f"/api/runs/{missing}/timing", headers=headers)
    assert resp.status_code == 404, resp.text
