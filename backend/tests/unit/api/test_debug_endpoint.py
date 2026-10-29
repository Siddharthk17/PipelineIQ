"""Debug endpoints must require admin even outside production (FZ03)."""

import uuid


def _register_login(auth_client, email, username):
    auth_client.post(
        "/auth/register",
        json={"email": email, "username": username,
              "password": "Testpass123!"},
    )
    resp = auth_client.post(
        "/auth/login", json={"email": email, "password": "Testpass123!"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_debug_sentry_requires_admin(auth_client, test_db):
    from backend.models import User

    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={"email": f"a_{suffix}@t.local", "username": f"a_{suffix}",
              "password": "Testpass123!"},
    )
    auth_client.post(
        "/auth/register",
        json={"email": f"v_{suffix}@t.local", "username": f"v_{suffix}",
              "password": "Testpass123!"},
    )
    # first registered user is the admin; second is a viewer
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"v_{suffix}@t.local", "password": "Testpass123!"})
    viewer_headers = {
        "Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = auth_client.get("/debug/sentry-test")
    assert resp.status_code in (401, 403), resp.text

    resp = auth_client.get("/debug/sentry-test", headers=viewer_headers)
    assert resp.status_code == 403, resp.text

    # An admin passes the guard and reaches the deliberate raise
    # (TestClient re-raises it instead of returning a 500).
    admin = test_db.query(User).filter_by(
        email=f"a_{suffix}@t.local").one()
    admin.role = "admin"
    test_db.commit()
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"a_{suffix}@t.local", "password": "Testpass123!"})
    admin_headers = {
        "Authorization": f"Bearer {resp.json()['access_token']}"}
    import pytest

    with pytest.raises(ValueError, match="Sentry test error"):
        auth_client.get("/debug/sentry-test", headers=admin_headers)
