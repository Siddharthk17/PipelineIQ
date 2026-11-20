"""Wave-3 authz regression tests (M-064, M-076, M-093, M-105, M-150, M-153).

Covers the authorization surfaces owned by the w3-authz worktree:
shared authz helper (backend/api/authz.py), permission endpoints
(backend/api/permissions.py), version endpoints (backend/api/versions.py),
and the worker-side consumer matcher (backend/contracts/enforcement.py).

Uses the sqlite-backed ``auth_client`` fixture with real JWT auth.
Celery tasks are monkeypatched so no broker is required.
"""

import uuid
from types import SimpleNamespace

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


def _upload(auth_client, headers, filename="sales.csv"):
    import io

    content = b"id,val\n1,10\n2,20\n"
    upload = auth_client.post(
        "/api/v1/files/upload",
        files={"file": (filename, io.BytesIO(content), "text/csv")},
        headers=headers,
    )
    assert upload.status_code == 201, upload.text
    return upload.json()["id"]


def _run_named(auth_client, headers, name, file_id, monkeypatch,
               yaml_name=None, expect=202):
    import backend.api.pipelines as pipelines_mod

    monkeypatch.setattr(
        pipelines_mod.execute_pipeline_task,
        "delay",
        lambda *a, **k: SimpleNamespace(id="test-task-id"),
    )
    yaml_config = (
        "pipeline:\n"
        f"  name: {yaml_name or name}\n"
        "  steps:\n"
        "    - name: src\n"
        "      type: load\n"
        f"      file_id: {file_id}\n"
    )
    payload = {"yaml_config": yaml_config}
    if name is not None:
        payload["name"] = name
    run = auth_client.post(
        "/api/v1/pipelines/run",
        json=payload,
        headers=headers,
    )
    assert run.status_code == expect, run.text
    return run


def _two_users(auth_client):
    tag = uuid.uuid4().hex[:8]
    victim_email = f"victim_{tag}@t.local"
    attacker_email = f"attacker_{tag}@t.local"
    _register(auth_client, victim_email, f"victim_{tag}")
    _register(auth_client, attacker_email, f"attacker_{tag}")
    return (
        _login(auth_client, victim_email),
        _login(auth_client, attacker_email),
        tag,
    )


# --- M-076: worker-side consumer matcher must be owner-scoped ---


def test_m076_cross_tenant_consumer_does_not_block_victim_schedule(
    auth_client, test_db, monkeypatch,
):
    """A breach whose consumers name a shared pipeline name must only block
    schedules owned by the breaching contract's owner (M-076 remainder)."""
    from backend.models import PipelineSchedule, User
    from backend.tasks import pipeline_tasks as tasks_mod

    _, _, tag = _two_users(auth_client)
    shared = f"shared_pipe_{tag}"

    victim = test_db.query(User).filter(
        User.email.like(f"victim_{tag}@%")).first()
    attacker = test_db.query(User).filter(
        User.email.like(f"attacker_{tag}@%")).first()
    assert victim is not None and attacker is not None

    for owner in (victim, attacker):
        test_db.add(
            PipelineSchedule(
                user_id=owner.id,
                pipeline_name=shared,
                yaml_config="pipeline:\n  name: x\n",
                cron_expression="* * * * *",
                is_active=True,
            )
        )
    test_db.commit()

    blocked = tasks_mod._block_downstream_schedules(
        test_db,
        shared,
        [shared],
        owner_id=attacker.id,
    )
    assert blocked == 1, "only the attacker's own schedule may be blocked"

    victim_sched = test_db.query(PipelineSchedule).filter(
        PipelineSchedule.user_id == victim.id,
        PipelineSchedule.pipeline_name == shared,
    ).first()
    assert victim_sched.last_run_status != "BLOCKED_BY_CONTRACT", (
        "M-076: cross-tenant consumer blocked the victim's schedule"
    )
    assert victim_sched.is_active is True


# --- M-064: canonical name used by permission check AND version save ---


def test_m064_run_name_and_yaml_name_resolve_to_single_identity(
    auth_client, test_db, monkeypatch,
):
    """When the request name and the YAML name differ, the permission grant
    and the saved version must use the same canonical identity (M-064)."""
    from backend.api.authz import canonical_pipeline_name
    from backend.models import PipelineVersion
    from backend.tasks import pipeline_tasks as tasks_mod

    victim_headers, _, tag = _two_users(auth_client)
    file_id = _upload(auth_client, victim_headers)
    req_name = f"req_{tag}"
    yaml_name = f"yaml_{tag}"
    resp = _run_named(
        auth_client, victim_headers, req_name, file_id, monkeypatch,
        yaml_name=yaml_name,
    )
    run_id = resp.json()["run_id"]

    from backend.models import PipelineRun
    from backend.utils.uuid_utils import as_uuid

    run = test_db.query(PipelineRun).filter(
        PipelineRun.id == as_uuid(run_id)).first()
    assert run is not None
    canonical = canonical_pipeline_name(req_name, yaml_name)
    assert run.name == canonical

    tasks_mod._save_version_if_needed(db=test_db, pipeline_run=run)
    versions = test_db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_name == canonical).all()
    assert len(versions) == 1, (
        "M-064: version must be saved under the canonical request name, "
        "not the divergent YAML name"
    )
    stray = test_db.query(PipelineVersion).filter(
        PipelineVersion.pipeline_name == yaml_name).all()
    assert not stray, "M-064: no version may be saved under the YAML name"


def test_m064_versions_endpoint_serves_canonical_name(
    auth_client, test_db, monkeypatch,
):
    """Versions listed under the canonical name the permission was granted
    on (M-064 end-to-end through the API surface)."""
    from backend.models import PipelineRun
    from backend.tasks import pipeline_tasks as tasks_mod
    from backend.utils.uuid_utils import as_uuid as _as_uuid2

    victim_headers, _, tag = _two_users(auth_client)
    file_id = _upload(auth_client, victim_headers)
    req_name = f"reqv_{tag}"
    resp = _run_named(
        auth_client, victim_headers, req_name, file_id, monkeypatch,
        yaml_name=f"yamlv_{tag}",
    )
    run = test_db.query(PipelineRun).filter(
        PipelineRun.id == _as_uuid2(resp.json()["run_id"])).first()
    tasks_mod._save_version_if_needed(db=test_db, pipeline_run=run)

    listed = auth_client.get(
        f"/api/v1/versions/{req_name}", headers=victim_headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total_versions"] == 1


# --- M-093: consistent ownership checks across surfaces ---


def _victim_pipeline_with_contract(auth_client, monkeypatch, tag):
    victim_headers, attacker_headers, _ = _two_users(auth_client)
    file_id = _upload(auth_client, victim_headers)
    name = f"vpipe_{tag}_{uuid.uuid4().hex[:6]}"
    _run_named(auth_client, victim_headers, name, file_id, monkeypatch)
    create = auth_client.post(
        f"/api/v1/contracts/pipelines/{name}",
        json={"yaml_content": YAML},
        headers=victim_headers,
    )
    assert create.status_code == 201, create.text
    return victim_headers, attacker_headers, name, create.json()["id"]


def test_m093_stranger_gets_no_access_on_version_surface(
    auth_client, monkeypatch,
):
    """Versions: owner reads, stranger gets anti-oracle 404 (M-093/M-150)."""
    tag = uuid.uuid4().hex[:8]
    victim_headers, attacker_headers, name, _ = (
        _victim_pipeline_with_contract(auth_client, monkeypatch, tag)
    )
    own = auth_client.get(
        f"/api/v1/versions/{name}", headers=victim_headers)
    assert own.status_code == 200, own.text
    stranger = auth_client.get(
        f"/api/v1/versions/{name}", headers=attacker_headers)
    assert stranger.status_code == 404, stranger.text


def test_m093_stranger_gets_no_access_on_contract_surface(
    auth_client, monkeypatch,
):
    """Contracts: stranger must never read a victim contract (M-093)."""
    tag = uuid.uuid4().hex[:8]
    victim_headers, attacker_headers, name, contract_id = (
        _victim_pipeline_with_contract(auth_client, monkeypatch, tag)
    )
    assert contract_id, "setup: contract creation must return an id"
    own = auth_client.get(
        f"/api/v1/contracts/pipelines/{name}",
        headers=victim_headers,
    )
    assert own.status_code == 200, own.text
    assert own.json()["total"] >= 1
    stranger = auth_client.get(
        f"/api/v1/contracts/pipelines/{name}",
        headers=attacker_headers,
    )
    assert stranger.status_code in (403, 404), stranger.text


def test_m093_stranger_gets_no_access_on_lineage_surface(
    auth_client, monkeypatch,
):
    """Lineage: stranger must never read a victim run's graph (M-093)."""
    tag = uuid.uuid4().hex[:8]
    victim_headers, attacker_headers, _ = _two_users(auth_client)
    file_id = _upload(auth_client, victim_headers)
    name = f"lpipe_{tag}"
    run = _run_named(auth_client, victim_headers, name, file_id, monkeypatch)
    run_id = run.json()["run_id"]
    stranger = auth_client.get(
        f"/api/v1/lineage/{run_id}", headers=attacker_headers)
    assert stranger.status_code in (403, 404), stranger.text


def test_m093_contract_owner_keeps_version_access(
    auth_client, monkeypatch, test_db,
):
    """Owning the contract is ownership: version reads must allow the
    contract owner even without a separate permission row (M-093)."""
    from backend.models import PipelinePermission

    tag = uuid.uuid4().hex[:8]
    victim_headers, _, _ = _two_users(auth_client)
    file_id = _upload(auth_client, victim_headers)
    name = f"cpipe_{tag}"
    _run_named(auth_client, victim_headers, name, file_id, monkeypatch)
    create = auth_client.post(
        f"/api/v1/contracts/pipelines/{name}",
        json={"yaml_content": YAML},
        headers=victim_headers,
    )
    assert create.status_code == 201, create.text

    me = auth_client.get("/auth/me", headers=victim_headers)
    assert me.status_code == 200, me.text
    victim_id = me.json()["id"]
    # Drop every permission row: contract ownership alone must suffice.
    from backend.utils.uuid_utils import as_uuid as _as_uuid

    test_db.query(PipelinePermission).filter(
        PipelinePermission.pipeline_name == name,
        PipelinePermission.user_id == _as_uuid(victim_id),
    ).delete(synchronize_session=False)
    test_db.commit()
    remaining = test_db.query(PipelinePermission).filter(
        PipelinePermission.pipeline_name == name).all()
    assert not remaining

    resp = auth_client.get(
        f"/api/v1/versions/{name}", headers=victim_headers)
    assert resp.status_code == 200, resp.text


# --- M-153: no silent duplicate grants; last-owner guard ---


def _owned_pipeline(auth_client, monkeypatch, headers, tag, prefix="pp"):
    file_id = _upload(auth_client, headers, f"{prefix}.csv")
    name = f"{prefix}_{tag}_{uuid.uuid4().hex[:4]}"
    _run_named(auth_client, headers, name, file_id, monkeypatch)
    return name


def _user_id(auth_client, headers):
    me = auth_client.get("/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["id"]


def test_m153_duplicate_grant_is_rejected_not_silent(
    auth_client, monkeypatch,
):
    """Granting the same level twice must be an explicit 409, and a level
    change must be applied and audited (M-153/M-150)."""
    from unittest.mock import patch

    import backend.api.permissions as permissions_mod

    owner_headers, grantee_headers, tag = _two_users(auth_client)
    name = _owned_pipeline(auth_client, monkeypatch, owner_headers, tag)
    target = _user_id(auth_client, grantee_headers)

    calls = []
    real_log_action = permissions_mod.log_action

    def spy_log_action(db, action, **kwargs):
        calls.append(action)
        return real_log_action(db, action, **kwargs)

    with patch.object(permissions_mod, "log_action", spy_log_action):
        first = auth_client.post(
            f"/api/v1/pipelines/{name}/permissions",
            json={"user_id": target, "permission_level": "runner"},
            headers=owner_headers,
        )
        assert first.status_code == 201, first.text

        duplicate = auth_client.post(
            f"/api/v1/pipelines/{name}/permissions",
            json={"user_id": target, "permission_level": "runner"},
            headers=owner_headers,
        )
        assert duplicate.status_code == 409, duplicate.text

        change = auth_client.post(
            f"/api/v1/pipelines/{name}/permissions",
            json={"user_id": target, "permission_level": "viewer"},
            headers=owner_headers,
        )
        assert change.status_code in (200, 201), change.text
        assert change.json()["permission_level"] == "viewer"

    # Level changes are role changes: they must leave an audit trail.
    assert "permission_granted" in calls
    assert "permission_updated" in calls, (
        "M-150: grant level change was not audited"
    )


def test_m153_revoke_last_owner_is_refused(auth_client, monkeypatch):
    """Revoking the final OWNER grant must fail closed (409) so a pipeline
    can never be orphaned with no owner (M-153)."""
    owner_headers, grantee_headers, tag = _two_users(auth_client)
    name = _owned_pipeline(auth_client, monkeypatch, owner_headers, tag)
    owner_id = _user_id(auth_client, owner_headers)
    target = _user_id(auth_client, grantee_headers)

    # Revoking a non-owner grant is fine.
    auth_client.post(
        f"/api/v1/pipelines/{name}/permissions",
        json={"user_id": target, "permission_level": "runner"},
        headers=owner_headers,
    )
    ok = auth_client.delete(
        f"/api/v1/pipelines/{name}/permissions/{target}",
        headers=owner_headers,
    )
    assert ok.status_code == 200, ok.text

    # Revoking the last owner grant must be refused.
    refused = auth_client.delete(
        f"/api/v1/pipelines/{name}/permissions/{owner_id}",
        headers=owner_headers,
    )
    assert refused.status_code == 409, refused.text


# --- M-105: grant race resolves to the single existing row ---


def test_m105_concurrent_grant_claim_resolves_to_one_row(
    auth_client, monkeypatch, test_db,
):
    """A grant racing an identical insert (IntegrityError on commit) must
    resolve to the existing row instead of 500ing (M-105)."""
    from unittest.mock import patch

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import sessionmaker

    from backend.models import PermissionLevel, PipelinePermission
    from backend.utils.uuid_utils import as_uuid

    owner_headers, grantee_headers, tag = _two_users(auth_client)
    name = _owned_pipeline(auth_client, monkeypatch, owner_headers, tag)
    target = _user_id(auth_client, grantee_headers)

    real_commit = test_db.commit
    state = {"failed": False}

    def flaky_commit():
        # Simulate the concurrent winner committing between our check and
        # our insert: a separate session inserts the same row, then our
        # commit raises the unique-violation the database would raise.
        if not state["failed"]:
            state["failed"] = True
            winner = sessionmaker(bind=test_db.bind)()
            winner.add(
                PipelinePermission(
                    pipeline_name=name,
                    user_id=as_uuid(target),
                    permission_level=PermissionLevel.RUNNER,
                )
            )
            winner.commit()
            winner.close()
            test_db.rollback()
            raise IntegrityError(
                "INSERT INTO pipeline_permissions", {}, Exception("duplicate"))
        return real_commit()

    with patch.object(test_db, "commit", flaky_commit):
        resp = auth_client.post(
            f"/api/v1/pipelines/{name}/permissions",
            json={"user_id": target, "permission_level": "runner"},
            headers=owner_headers,
        )
    # The raced insert won elsewhere: explicit duplicate signal, never 500.
    assert resp.status_code == 409, resp.text
    test_db.expire_all()
    rows = test_db.query(PipelinePermission).filter(
        PipelinePermission.pipeline_name == name,
        PipelinePermission.user_id == as_uuid(target),
    ).all()
    assert len(rows) == 1


# --- M-087: validator messages must not embed exception internals ---


def test_m087_breach_messages_hide_exception_internals():
    """Breach messages surfaced to users must be generic; raw exception text
    and unbounded value samples must not leak (M-087)."""
    import pyarrow as pa

    from backend.contracts.validator import validate_against_contract

    table = pa.table({"val": ["a", "b", "c"]})
    yaml_content = (
        "columns:\n"
        "  val:\n"
        "    min_value: 5\n"
        "    pattern: '[(' \n"
    )
    result = validate_against_contract(
        output_table=table, yaml_content=yaml_content)
    assert result.violations, "expected at least one violation"
    for v in result.violations:
        assert "Traceback" not in v.message
        assert "object at 0x" not in v.message
        assert "File \"" not in v.message
