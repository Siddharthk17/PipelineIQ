"""Regression tests: cross-tenant leaks via run export (M-004) and
schema-drift baseline (M-065).

M-004: ``GET /api/v1/pipelines/{run_id}/export`` resolved attacker-controlled
``save.filename`` / step names with :mod:`glob` over the shared ``UPLOAD_DIR``
and served the first match, so one tenant's run could export another tenant's
file. ``is_safe_filename`` also raised :exc:`TypeError` on non-string input
instead of returning ``False``.

M-065: the upload-path drift baseline matched the previous snapshot by
``original_filename`` only, so uploading ``report.csv`` returned another
tenant's column names/dtypes as drift.
"""

import uuid

import pytest

from backend.models import PipelineRun, PipelineStatus
from backend.utils.uuid_utils import as_uuid

PASSWORD = "Testpass123!"


def _register(auth_client, email, username):
    resp = auth_client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text


def _login(auth_client, email):
    resp = auth_client.post(
        "/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _upload(auth_client, headers, name, content):
    resp = auth_client.post(
        "/api/v1/files/upload",
        files={"file": (name, content, "text/csv")},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _run_yaml(name, file_id, save_filename, step_name="save_output"):
    return (
        f"pipeline:\n  name: {name}\n  steps:\n"
        f"    - name: load_data\n      type: load\n      file_id: \"{file_id}\"\n"
        f"    - name: {step_name}\n      type: save\n      input: load_data\n"
        f"      filename: \"{save_filename}\"\n"
    )


def _create_completed_run(auth_client, headers, test_db, name, file_id,
                           save_filename, step_name="save_output"):
    """Create a run via the API, then flip it to COMPLETED (no execution)."""
    resp = auth_client.post(
        "/api/v1/pipelines/run",
        json={
            "yaml_config": _run_yaml(
                name, file_id, save_filename, step_name=step_name),
            "name": name,
        },
        headers=headers,
    )
    assert resp.status_code == 202, resp.text
    run_id = resp.json()["run_id"]
    run = test_db.query(PipelineRun).filter(
        PipelineRun.id == as_uuid(run_id)).first()
    assert run is not None
    run.status = PipelineStatus.COMPLETED
    test_db.commit()
    return run_id


@pytest.fixture()
def users(auth_client):
    """Register a victim and an attacker; return (victim_headers, attacker_headers)."""
    tag = uuid.uuid4().hex[:8]
    _register(auth_client, f"victim_{tag}@t.local", f"victim_{tag}")
    _register(auth_client, f"attacker_{tag}@t.local", f"attacker_{tag}")
    return (_login(auth_client, f"victim_{tag}@t.local"),
            _login(auth_client, f"attacker_{tag}@t.local"))


class TestExportTenantIsolation:
    """M-004: attacker export must never serve another tenant's file."""

    def test_export_glob_filename_cannot_read_victim_file(
        self, auth_client, test_db, users, tmp_path, monkeypatch
    ):
        """save.filename='*' must not match the victim's file (404, no leak)."""
        _, attacker_headers = users
        shared = tmp_path / "shared"
        shared.mkdir()
        victim_bytes = b"victim_id,victim_secret\n1,zzz-top-secret\n"
        (shared / "victim_secret.csv").write_bytes(victim_bytes)
        monkeypatch.setattr(
            "backend.api.pipelines.settings.UPLOAD_DIR", shared)

        file_id = _upload(auth_client, attacker_headers,
                          "atk.csv", b"id,val\n1,2\n")
        run_id = _create_completed_run(
            auth_client, attacker_headers, test_db,
            f"atk_{uuid.uuid4().hex[:8]}", file_id, "*")

        resp = auth_client.get(
            f"/api/v1/pipelines/{run_id}/export", headers=attacker_headers)
        assert resp.status_code == 404, (
            f"export must not resolve glob filename: {resp.status_code}")
        assert victim_bytes not in resp.content

    def test_export_same_stem_cannot_read_victim_output(
        self, auth_client, test_db, users, tmp_path, monkeypatch
    ):
        """Same save stem as the victim's output must not serve victim bytes."""
        _, attacker_headers = users
        shared = tmp_path / "shared"
        shared.mkdir()
        victim_bytes = b"order_id,victim_col\n9,leaked\n"
        (shared / "output.csv_deadbeef0123.csv").write_bytes(victim_bytes)
        monkeypatch.setattr(
            "backend.api.pipelines.settings.UPLOAD_DIR", shared)

        file_id = _upload(auth_client, attacker_headers,
                          "atk.csv", b"id,val\n1,2\n")
        run_id = _create_completed_run(
            auth_client, attacker_headers, test_db,
            f"atk_{uuid.uuid4().hex[:8]}", file_id, "output.csv")

        resp = auth_client.get(
            f"/api/v1/pipelines/{run_id}/export", headers=attacker_headers)
        assert resp.status_code == 404, (
            f"export must be scoped to the run's own outputs: "
            f"{resp.status_code}")
        assert victim_bytes not in resp.content

    def test_export_traversal_filename_cannot_escape(
        self, auth_client, test_db, users, tmp_path, monkeypatch
    ):
        """A save filename with '..' must never resolve outside the run."""
        _, attacker_headers = users
        shared = tmp_path / "shared"
        shared.mkdir()
        victim_bytes = b"secret-payload\n"
        (shared / "victim_secret.csv").write_bytes(victim_bytes)
        monkeypatch.setattr(
            "backend.api.pipelines.settings.UPLOAD_DIR", shared)

        # Public API must reject traversal filenames at creation time.
        file_id = _upload(auth_client, attacker_headers,
                          "atk.csv", b"id,val\n1,2\n")
        name = f"atk_{uuid.uuid4().hex[:8]}"
        resp = auth_client.post(
            "/api/v1/pipelines/run",
            json={"yaml_config": _run_yaml(
                name, file_id, "../victim_secret"), "name": name},
            headers=attacker_headers,
        )
        assert resp.status_code == 400, resp.text

        # A stored run carrying a traversal filename must still export 404.
        from backend.models import User
        attacker = test_db.query(User).filter(
            User.email.like("attacker_%@t.local")).order_by(
                User.email.desc()).first()
        assert attacker is not None
        run = PipelineRun(
            name=f"evil_{uuid.uuid4().hex[:8]}",
            status=PipelineStatus.COMPLETED,
            yaml_config=_run_yaml(
                f"evil_{uuid.uuid4().hex[:4]}", file_id,
                "../victim_secret"),
            user_id=attacker.id,
        )
        test_db.add(run)
        test_db.commit()
        run_id = str(run.id)

        resp = auth_client.get(
            f"/api/v1/pipelines/{run_id}/export", headers=attacker_headers)
        assert resp.status_code == 404, (
            f"traversal save filename must not resolve: {resp.status_code}")
        assert victim_bytes not in resp.content

    def test_export_serves_run_scoped_artifact(
        self, auth_client, test_db, users, tmp_path, monkeypatch
    ):
        """Functionality pin: a run-id-prefixed artifact stays downloadable."""
        _, attacker_headers = users
        shared = tmp_path / "shared"
        shared.mkdir()
        monkeypatch.setattr(
            "backend.api.pipelines.settings.UPLOAD_DIR", shared)

        file_id = _upload(auth_client, attacker_headers,
                          "atk.csv", b"id,val\n1,2\n")
        run_id = _create_completed_run(
            auth_client, attacker_headers, test_db,
            f"atk_{uuid.uuid4().hex[:8]}", file_id, "output.csv")

        own_bytes = b"order_id,status\n1,delivered\n"
        (shared / f"{run_id}_out.csv").write_bytes(own_bytes)
        resp = auth_client.get(
            f"/api/v1/pipelines/{run_id}/export", headers=attacker_headers)
        assert resp.status_code == 200, resp.text
        assert resp.content == own_bytes


class TestIsSafeFilename:
    """M-004: is_safe_filename must fail closed on non-string input."""

    @pytest.mark.parametrize("bad", [12345, None, ["a"], ("a",), {"a": 1},
                                        b"a/b", 3.14, True])
    def test_non_string_returns_false(self, bad):
        from backend.utils.string_utils import is_safe_filename
        assert is_safe_filename(bad) is False

    @pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", "/"])
    def test_unsafe_names_rejected(self, bad):
        from backend.utils.string_utils import is_safe_filename
        assert is_safe_filename(bad) is False

    def test_plain_name_accepted(self):
        from backend.utils.string_utils import is_safe_filename
        assert is_safe_filename("output.csv") is True


class TestDriftBaselineOwnerScope:
    """M-065: drift baseline must only consider the uploader's own snapshots."""

    def test_upload_same_name_as_other_tenant_has_no_drift(
        self, auth_client, users
    ):
        victim_headers, attacker_headers = users
        victim_resp = auth_client.post(
            "/api/v1/files/upload",
            files={"file": ("report.csv", b"id,val\n1,10\n", "text/csv")},
            headers=victim_headers,
        )
        assert victim_resp.status_code == 201, victim_resp.text

        attacker_resp = auth_client.post(
            "/api/v1/files/upload",
            files={"file": ("report.csv", b"id,extra\n1,foo\n", "text/csv")},
            headers=attacker_headers,
        )
        assert attacker_resp.status_code == 201, attacker_resp.text
        payload = attacker_resp.json()
        assert payload["schema_drift"] is None, (
            f"drift baseline leaked another tenant's schema: "
            f"{payload['schema_drift']}")

    def test_reupload_own_file_still_drifts(self, auth_client, users):
        """Functionality pin: same-owner re-upload still compares to baseline."""
        victim_headers, _ = users
        first = auth_client.post(
            "/api/v1/files/upload",
            files={"file": ("mine.csv", b"id,val\n1,10\n", "text/csv")},
            headers=victim_headers,
        )
        assert first.status_code == 201, first.text
        assert first.json()["schema_drift"] is None
        second = auth_client.post(
            "/api/v1/files/upload",
            files={"file": ("mine.csv", b"id,val,extra\n1,10,x\n",
                             "text/csv")},
            headers=victim_headers,
        )
        assert second.status_code == 201, second.text
        drift = second.json()["schema_drift"]
        assert drift is not None and drift["has_drift"] is True
