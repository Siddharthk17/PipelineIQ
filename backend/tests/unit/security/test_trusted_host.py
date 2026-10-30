"""Host header attacks must not reach the app (S12-adjacent)."""

import uuid


def test_unknown_host_rejected(auth_client):
    resp = auth_client.get("/healthz", headers={"host": "evil.example"})
    assert resp.status_code == 400, resp.text


def test_known_host_allowed(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={"email": f"h_{suffix}@t.local", "username": f"h_{suffix}",
              "password": "Testpass123!"},
        headers={"host": "localhost"},
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"h_{suffix}@t.local", "password": "Testpass123!"},
        headers={"host": "localhost"},
    )
    assert resp.status_code == 200, resp.text
