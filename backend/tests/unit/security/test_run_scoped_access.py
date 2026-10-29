"""Run-scoped access consistency: grantees, admins, UUID shape (S20g)."""

import uuid

import pytest
from fastapi import HTTPException


def _register_login(auth_client, email, username):
    auth_client.post(
        "/auth/register",
        json={"email": email, "username": username,
              "password": "Testpass123!"},
    )
    resp = auth_client.post(
        "/auth/login", json={"email": email, "password": "Testpass123!"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _ns(**kwargs):
    from types import SimpleNamespace

    return SimpleNamespace(**kwargs)


def test_sse_authorize_allows_grantee(test_db):
    from backend.api import sse as sse_mod
    from backend.models import PermissionLevel, PipelinePermission

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    test_db.add(
        PipelinePermission(
            pipeline_name="shared-stream",
            user_id=viewer_id,
            permission_level=PermissionLevel.VIEWER,
        )
    )
    test_db.commit()
    run = _ns(name="shared-stream", user_id=owner_id)
    viewer = _ns(role="viewer", id=viewer_id)

    # No raise means authorized.
    sse_mod._authorize_run_access(run, viewer, test_db)

    stranger = _ns(role="viewer", id=uuid.uuid4())
    with pytest.raises(HTTPException) as exc:
        sse_mod._authorize_run_access(run, stranger, test_db)
    assert exc.value.status_code == 403

    admin = _ns(role="admin", id=uuid.uuid4())
    sse_mod._authorize_run_access(run, admin, test_db)

    orphan = _ns(name="shared-stream", user_id=None)
    with pytest.raises(HTTPException) as exc:
        sse_mod._authorize_run_access(orphan, viewer, test_db)
    assert exc.value.status_code == 403


def test_lineage_export_admin_bypass_and_uuid_shape(
    auth_client, test_db, monkeypatch, sales_csv_bytes
):
    from types import SimpleNamespace

    import backend.api.pipelines as pipelines_mod

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task, "delay",
        lambda *a, **k: SimpleNamespace(id="t"))
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={"email": f"a_{suffix}@t.local", "username": f"a_{suffix}",
              "password": "Testpass123!"})
    owner_headers = _register_login(auth_client, f"o_{suffix}@t.local",
                                    f"o_{suffix}")
    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": ("s.csv", sales_csv_bytes, "text/csv")},
        headers=owner_headers,
    )
    file_id = upload.json()["id"]
    run = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": (
            "pipeline:\n  name: lx\n  steps:\n"
            "    - name: src\n      type: load\n"
            f"      file_id: {file_id}\n")},
        headers=owner_headers,
    )
    run_id = run.json()["run_id"]

    # Admin (first user) can export another user's run.
    from backend.models import User

    admin = test_db.query(User).filter_by(
        email=f"a_{suffix}@t.local").one()
    admin.role = "admin"
    test_db.commit()
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"a_{suffix}@t.local", "password": "Testpass123!"})
    admin_headers = {
        "Authorization": f"Bearer {resp.json()['access_token']}"}

    resp = auth_client.get(f"/api/runs/{run_id}/openlineage",
                           headers=admin_headers)
    assert resp.status_code == 200, resp.text

    resp = auth_client.get("/api/runs/not-a-uuid/openlineage",
                           headers=admin_headers)
    assert resp.status_code == 422, resp.text


def test_column_policy_delete_garbage_uuid(auth_client):
    suffix = uuid.uuid4().hex[:8]
    headers = _register_login(auth_client, f"cp_{suffix}@t.local",
                              f"cp_{suffix}")
    # Use unique emails per run to avoid clashes.
    resp = auth_client.delete("/api/column-policies/not-a-uuid",
                              headers=headers)
    assert resp.status_code == 422, resp.text


def test_audit_logs_garbage_user_id(auth_client, test_db):
    from backend.models import User

    suffix = uuid.uuid4().hex[:8]
    _register_login(auth_client, f"au_{suffix}@t.local",
                              f"au_{suffix}")
    admin = test_db.query(User).filter_by(
        email=f"au_{suffix}@t.local").one()
    admin.role = "admin"
    test_db.commit()
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"au_{suffix}@t.local", "password": "Testpass123!"})
    admin_headers = {
        "Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = auth_client.get("/audit/logs?user_id=not-a-uuid",
                           headers=admin_headers)
    assert resp.status_code == 422, resp.text
