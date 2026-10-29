"""Regression tests for pipeline file ownership (S20f).

Referencing another user's file in a pipeline, schedule, validation,
preview or estimate must behave exactly like referencing a missing file:
no content, no metadata, no row counts leak across users.
"""

import uuid


def _register(auth_client, email, username):
    resp = auth_client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "Testpass123!"},
    )
    assert resp.status_code in (200, 201), resp.text


def _login(auth_client, email):
    resp = auth_client.post(
        "/auth/login", json={"email": email, "password": "Testpass123!"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _upload(auth_client, headers, sales_csv_bytes, name="s.csv"):
    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": (name, sales_csv_bytes, "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    return upload.json()["id"]


def _yaml(name, file_id):
    return (
        "pipeline:\n"
        f"  name: {name}\n"
        "  steps:\n"
        "    - name: src\n"
        "      type: load\n"
        f"      file_id: {file_id}\n"
    )


def _two_users(auth_client):
    suffix = uuid.uuid4().hex[:8]
    _register(auth_client, f"a_{suffix}@t.local", f"a_{suffix}")
    _register(auth_client, f"b_{suffix}@t.local", f"b_{suffix}")
    return (
        _login(auth_client, f"a_{suffix}@t.local"),
        _login(auth_client, f"b_{suffix}@t.local"),
    )


def test_run_with_foreign_file_id_404s(auth_client, monkeypatch, sales_csv_bytes):
    """User B cannot run a pipeline over user A's file."""
    import backend.api.pipelines as pipelines_mod
    from types import SimpleNamespace

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task, "delay",
        lambda *a, **k: SimpleNamespace(id="t"),
    )
    user_a, user_b = _two_users(auth_client)
    file_a = _upload(auth_client, user_a, sales_csv_bytes)
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={"yaml_config": _yaml("stolen", file_a)},
        headers=user_b,
    )
    assert resp.status_code == 404, resp.text


def test_validate_with_foreign_file_id_fails(auth_client, sales_csv_bytes):
    """Validation must not confirm a pipeline over someone else's file."""
    user_a, user_b = _two_users(auth_client)
    file_a = _upload(auth_client, user_a, sales_csv_bytes)
    resp = auth_client.post(
        "/api/v1/pipelines/validate",
        json={"yaml_config": _yaml("stolen", file_a)},
        headers=user_b,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_valid"] is False, resp.text


def test_schedule_with_foreign_file_id_rejected(
        auth_client, sales_csv_bytes):
    """Schedules cannot be created over another user's file."""
    user_a, user_b = _two_users(auth_client)
    file_a = _upload(auth_client, user_a, sales_csv_bytes)
    resp = auth_client.post(
        "/api/v1/schedules/",
        json={
            "pipeline_name": f"sched_{uuid.uuid4().hex[:8]}",
            "yaml_config": _yaml("sched", file_a),
            "cron_expression": "0 0 * * *",
        },
        headers=user_b,
    )
    assert resp.status_code in (400, 404), resp.text


def test_estimate_ignores_foreign_files(auth_client, sales_csv_bytes):
    """Estimates with only foreign files fall back exactly like unknown files."""
    user_a, user_b = _two_users(auth_client)
    file_a = _upload(auth_client, user_a, sales_csv_bytes)
    foreign = auth_client.post(
        "/api/runs/estimate",
        json={
            "pipeline_yaml": _yaml("est", file_a),
            "file_ids": [file_a],
        },
        headers=user_b,
    )
    unknown = auth_client.post(
        "/api/runs/estimate",
        json={
            "pipeline_yaml": _yaml("est", "00000000-0000-0000-0000-000000000000"),
            "file_ids": ["00000000-0000-0000-0000-000000000000"],
        },
        headers=user_b,
    )
    assert foreign.status_code == 200, foreign.text
    assert unknown.status_code == 200, unknown.text
    assert foreign.json()["total_ms"] == unknown.json()["total_ms"]
