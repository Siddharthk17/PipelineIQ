"""Regression tests for self-serve pipeline creation (S47).

Any authenticated non-admin user running a brand-new pipeline name must
be granted ownership. Viewers are the default role for every signup; if
they can never create a pipeline, the signup-to-first-run journey is dead.
"""

import uuid

from backend.models import PipelinePermission


def _register(auth_client, email, username):
    resp = auth_client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "password": "Testpass123!",
        },
    )
    assert resp.status_code in (200, 201), resp.text


def _login(auth_client, email):
    resp = auth_client.post(
        "/auth/login",
        json={"email": email, "password": "Testpass123!"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_viewer_run_new_pipeline_grants_owner(
    auth_client, test_db, monkeypatch, sales_csv_bytes
):
    """A viewer running a new pipeline name gets a 202 and an owner grant."""
    from types import SimpleNamespace

    import backend.api.pipelines as pipelines_mod

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task,
        "delay",
        lambda *a, **k: SimpleNamespace(id="test-task-id"),
    )

    suffix = uuid.uuid4().hex[:8]
    _register(auth_client, f"admin_{suffix}@t.local", f"admin_{suffix}")
    _register(auth_client, f"viewer_{suffix}@t.local", f"viewer_{suffix}")
    viewer_token = _login(auth_client, f"viewer_{suffix}@t.local")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    file_id = upload.json()["id"]

    pipeline_name = f"viewer_pipe_{suffix}"
    yaml_config = (
        "pipeline:\n"
        f"  name: {pipeline_name}\n"
        "  steps:\n"
        "    - name: src\n"
        "      type: load\n"
        f"      file_id: {file_id}\n"
    )
    run = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": yaml_config},
        headers=headers,
    )
    assert run.status_code == 202, run.text

    viewer_id = test_db.query(
        __import__("backend.models", fromlist=["User"]).User
    ).filter_by(email=f"viewer_{suffix}@t.local").one().id
    grant = (
        test_db.query(PipelinePermission)
        .filter(
            PipelinePermission.pipeline_name == pipeline_name,
            PipelinePermission.user_id == viewer_id,
        )
        .one_or_none()
    )
    assert grant is not None
    assert grant.permission_level in ("owner", "OWNER")


def test_viewer_rerun_own_pipeline_allowed(
    auth_client, monkeypatch, sales_csv_bytes
):
    """A viewer who owns a pipeline name can run it again."""
    from types import SimpleNamespace

    import backend.api.pipelines as pipelines_mod

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task,
        "delay",
        lambda *a, **k: SimpleNamespace(id="test-task-id"),
    )

    suffix = uuid.uuid4().hex[:8]
    _register(auth_client, f"admin2_{suffix}@t.local", f"admin2_{suffix}")
    _register(auth_client, f"viewer2_{suffix}@t.local", f"viewer2_{suffix}")
    viewer_token = _login(auth_client, f"viewer2_{suffix}@t.local")
    headers = {"Authorization": f"Bearer {viewer_token}"}

    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        headers=headers,
    )
    file_id = upload.json()["id"]
    pipeline_name = f"viewer_pipe2_{suffix}"
    yaml_config = (
        "pipeline:\n"
        f"  name: {pipeline_name}\n"
        "  steps:\n"
        "    - name: src\n"
        "      type: load\n"
        f"      file_id: {file_id}\n"
    )
    first = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": yaml_config},
        headers=headers,
    )
    assert first.status_code == 202, first.text
    second = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": yaml_config},
        headers=headers,
    )
    assert second.status_code == 202, second.text
