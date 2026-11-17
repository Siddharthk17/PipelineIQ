"""RBAC permission tests exercised with real authentication (M-051).

Replaces the permanently-skipped live-HTTP suite
(``backend/tests/unit/security/test_permissions.py``, which requires a
running Docker stack and is therefore never executed by CI) with
equivalent tests running against ``TestClient`` + real JWTs. Assertions
(intentionally) mirror the original suite: no weakening.
"""

import uuid

import jwt

from backend.auth import get_current_admin, get_current_user, get_optional_user
from backend.config import settings
from backend.main import app
from backend.models import User

PASSWORD = "Testpass123!"


def _register(auth_client, email, username):
    resp = auth_client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _login(auth_client, email):
    resp = auth_client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["user"]


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


def _two_users(auth_client):
    """Register admin (first) + viewer (second); return (admin, viewer) headers."""
    tag = uuid.uuid4().hex[:8]
    admin_email = f"perm_admin_{tag}@t.local"
    viewer_email = f"perm_viewer_{tag}@t.local"
    _register(auth_client, admin_email, f"perm_admin_{tag}")
    _register(auth_client, viewer_email, f"perm_viewer_{tag}")
    admin_token, _ = _login(auth_client, admin_email)
    viewer_token, viewer_user = _login(auth_client, viewer_email)
    return (
        {"Authorization": f"Bearer {admin_token}"},
        {"Authorization": f"Bearer {viewer_token}"},
        viewer_user,
    )


def test_viewer_self_serve_run_grant_revoke_cycle(auth_client):
    """Viewer lifecycle: self-serve own name, denied on admin name, granted,
    revoked — mirroring the skipped live-stack test_permissions."""
    admin_headers, viewer_headers, viewer_user = _two_users(auth_client)
    tag = uuid.uuid4().hex[:8]
    viewer_file_id = _upload(auth_client, viewer_headers, "viewer.csv")
    admin_file_id = _upload(auth_client, admin_headers, "admin.csv")

    # Viewer runs a brand-new pipeline name (self-serve: becomes owner).
    own_name = f"viewer_own_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(own_name, viewer_file_id), "name": own_name},
        headers=viewer_headers,
    )
    assert resp.status_code == 202, resp.text

    # Admin runs their own pipeline; viewer has no grant on it.
    admin_name = f"admin_owned_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(admin_name, admin_file_id), "name": admin_name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text

    # Viewer tries the admin's pipeline without a grant (must fail).
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(admin_name, viewer_file_id), "name": admin_name},
        headers=viewer_headers,
    )
    assert resp.status_code == 404, resp.text

    # Admin grants runner permission.
    resp = auth_client.post(
        f"/api/v1/pipelines/{admin_name}/permissions",
        json={"user_id": viewer_user["id"], "permission_level": "runner"},
        headers=admin_headers,
    )
    assert resp.status_code in (200, 201), resp.text

    # Viewer runs the shared pipeline (must succeed).
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(admin_name, viewer_file_id), "name": admin_name},
        headers=viewer_headers,
    )
    assert resp.status_code == 202, resp.text

    # Admin revokes permission.
    resp = auth_client.delete(
        f"/api/v1/pipelines/{admin_name}/permissions/{viewer_user['id']}",
        headers=admin_headers,
    )
    assert resp.status_code in (200, 204), resp.text

    # Viewer tries again (must fail).
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(admin_name, viewer_file_id), "name": admin_name},
        headers=viewer_headers,
    )
    assert resp.status_code == 404, resp.text


def test_viewer_role_cannot_administer_pipeline_permissions(auth_client):
    """A viewer (non-owner, non-admin) cannot grant permissions."""
    admin_headers, viewer_headers, viewer_user = _two_users(auth_client)
    tag = uuid.uuid4().hex[:8]
    admin_file_id = _upload(auth_client, admin_headers, "admin.csv")
    admin_name = f"perm_target_{tag}"
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _pipeline_yaml(admin_name, admin_file_id), "name": admin_name},
        headers=admin_headers,
    )
    assert resp.status_code == 202, resp.text

    resp = auth_client.post(
        f"/api/v1/pipelines/{admin_name}/permissions",
        json={"user_id": viewer_user["id"], "permission_level": "runner"},
        headers=viewer_headers,
    )
    assert resp.status_code == 403, resp.text


def test_client_fixture_exercises_real_authentication(client, test_db):
    """Guard for M-052: the default ``client`` fixture must present a real
    JWT verified through the production auth path — never a mock-admin
    dependency override."""
    for dep in (get_current_user, get_current_admin, get_optional_user):
        assert dep not in app.dependency_overrides, (
            f"client fixture overrides {dep.__name__} with a mock user"
        )
    default_auth = client.headers.get("Authorization")
    assert default_auth and default_auth.startswith("Bearer "), (
        "client sends no default Authorization header"
    )
    payload = jwt.decode(default_auth.split(" ", 1)[1], settings.SECRET_KEY, algorithms=["HS256"])
    import uuid as _uuid

    user = test_db.query(User).filter(User.id == _uuid.UUID(payload["sub"])).first()
    assert user is not None, "client token sub is not a real database user"
    assert user.role == "admin"

    forged = client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert forged.status_code == 401, forged.text
    me = client.get("/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["email"] == user.email
