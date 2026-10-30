"""Regression test for WASM upload robustness (FZ02).

An invalid binary must be rejected with a client error, never a 500.
Mirrors the validate endpoint, which already maps this to is_valid=false.
"""

import uuid


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"fz2_{suffix}@t.local",
            "username": f"fz2_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"fz2_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_upload_garbage_bytes_returns_400_not_500(auth_client):
    headers = _register_login(auth_client)
    resp = auth_client.post(
        "/api/v1/wasm/upload?name=badmod",
        files={"file": ("bad.wasm", b"\x00\x01\x02not-a-wasm-module", "application/wasm")},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "Wasm" in resp.text or "wasm" in resp.text or "binary" in resp.text
