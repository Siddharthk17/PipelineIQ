"""Regression tests for contract same-name bypass (S20).

Pipeline identity is name-based. Owning a run that merely shares a name
with someone else's pipeline must not grant any access to their
contracts — not reads, and especially not owner-level writes.
"""

import uuid

YAML = "contracts:\n  - column: a\n    rule: not_null\n"


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
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _upload(auth_client, headers, sales_csv_bytes):
    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    return upload.json()["id"]


def _run_named(auth_client, headers, name, file_id, monkeypatch,
             expect=202):
    import backend.api.pipelines as pipelines_mod
    from types import SimpleNamespace

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task,
        "delay",
        lambda *a, **k: SimpleNamespace(id="test-task-id"),
    )
    yaml_config = (
        "pipeline:\n"
        f"  name: {name}\n"
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
    assert run.status_code == expect, run.text
    return run


def _victim_with_contract(auth_client, monkeypatch, sales_csv_bytes, name):
    suffix = uuid.uuid4().hex[:8]
    victim_email = f"victim_{suffix}@t.local"
    _register(auth_client, victim_email, f"victim_{suffix}")
    victim = _login(auth_client, victim_email)
    file_id = _upload(auth_client, victim, sales_csv_bytes)
    _run_named(auth_client, victim, name, file_id, monkeypatch)
    create = auth_client.post(
        f"/api/v1/contracts/pipelines/{name}",
        json={"yaml_content": YAML},
        headers=victim,
    )
    assert create.status_code == 201, create.text
    contract_id = create.json()["id"]
    return victim, victim_email, contract_id


def test_same_name_run_grants_no_contract_read(
    auth_client, monkeypatch, sales_csv_bytes
):
    """User B with a same-named run cannot list user A's contracts."""
    suffix = uuid.uuid4().hex[:8]
    name = f"shared_{suffix}"
    _victim_with_contract(auth_client, monkeypatch, sales_csv_bytes, name)

    _register(auth_client, f"attacker_{suffix}@t.local", f"attacker_{suffix}")
    attacker = _login(auth_client, f"attacker_{suffix}@t.local")
    file_id = _upload(auth_client, attacker, sales_csv_bytes)
    _run_named(auth_client, attacker, name, file_id, monkeypatch, expect=403)

    listed = auth_client.get(
        f"/api/v1/contracts/pipelines/{name}", headers=attacker)
    assert listed.status_code == 403, listed.text


def test_same_name_run_grants_no_contract_write(
    auth_client, monkeypatch, sales_csv_bytes
):
    """User B with a same-named run cannot version or delete A's contract."""
    suffix = uuid.uuid4().hex[:8]
    name = f"sharedw_{suffix}"
    _, victim_email, contract_id = _victim_with_contract(
        auth_client, monkeypatch, sales_csv_bytes, name)

    _register(auth_client, f"attackerw_{suffix}@t.local", f"attackerw_{suffix}")
    attacker = _login(auth_client, f"attackerw_{suffix}@t.local")
    file_id = _upload(auth_client, attacker, sales_csv_bytes)
    _run_named(auth_client, attacker, name, file_id, monkeypatch, expect=403)

    updated = auth_client.put(
        f"/api/v1/contracts/pipelines/{name}/{contract_id}",
        json={"yaml_content": YAML},
        headers=attacker,
    )
    assert updated.status_code == 403, updated.text

    deleted = auth_client.delete(
        f"/api/v1/contracts/pipelines/{name}/{contract_id}",
        headers=attacker,
    )
    assert deleted.status_code == 403, deleted.text

    # Victim still owns and manages their contract.
    victim = _login(auth_client, victim_email)
    listed = auth_client.get(
        f"/api/v1/contracts/pipelines/{name}", headers=victim)
    assert listed.status_code == 200, listed.text


def test_garbage_run_id_is_not_a_server_error(auth_client):
    """Malformed UUIDs on run-scoped contract reads must not 500."""
    _register(auth_client, "g1x@t.local", "g1x")
    headers = _login(auth_client, "g1x@t.local")
    resp = auth_client.get(
        "/api/v1/contracts/runs/not-a-uuid", headers=headers)
    assert resp.status_code in (404, 422), resp.text
