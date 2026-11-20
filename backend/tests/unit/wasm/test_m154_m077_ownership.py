"""Regression tests for M-154 (name oracle) and M-077 (ownership wiring).

M-154: WasmModule.name was globally unique in the DB while the
application-level duplicate check was per-user. Resolved by scoping
names per user (composite unique constraint, migration 0019): a second
user uploading an already-taken name succeeds with their own module
(200, distinct id), while the SAME user re-uploading a name gets a
clean 409 — never a 500 from the global UNIQUE constraint, and never
an error that reveals who owns the name.

M-077: ownership helpers (get_owned_wasm_module / assert_wasm_ids_owned,
added in Wave 1) must be wired at pipeline-save validation and run-start
loading time. The wiring helpers live in backend/routers/wasm.py:
extract_wasm_ids_from_yaml, validate_pipeline_wasm_ownership and
load_owned_wasm_modules_for_run. Cross-owner references resolve to 404
(no oracle: missing and foreign ids are indistinguishable).
"""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.routers.wasm import (
    assert_wasm_ids_owned,
    get_owned_wasm_module,
)


def _register_user(auth_client, tag):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"{tag}_{suffix}@t.local",
            "username": f"{tag}_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"{tag}_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _register_second_user(auth_client):
    return _register_user(auth_client, "m154")


def _upload(client, headers, wasm_bytes, name):

    return client.post(
        "/api/v1/wasm/upload",
        files={"file": ("m.wasm", wasm_bytes, "application/wasm")},
        data={"name": name},
        headers=headers,
    )


class TestNameConflictHandling:
    def test_second_user_same_name_is_allowed_distinct_module(
        self, auth_client, simple_add_wasm, monkeypatch
    ):
        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)

        headers_a = _register_user(auth_client, "m154a")
        headers_b = _register_second_user(auth_client)

        first = _upload(auth_client, headers_a, simple_add_wasm, "shared-name")
        assert first.status_code == 200, first.text

        second = _upload(auth_client, headers_b, simple_add_wasm, "shared-name")
        # Names are scoped per user (composite unique constraint,
        # migration 0019): the second user gets their own module (200 with
        # a distinct id) — never a 500 integrity-error traceback, and the
        # response reveals nothing about the other owner's module.
        assert second.status_code == 200, second.text
        assert second.json()["id"] != first.json()["id"]
        assert second.json()["name"] == "shared-name"

    def test_same_user_duplicate_name_is_409(self, client, simple_add_wasm, monkeypatch):

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)

        first = _upload(client, {}, simple_add_wasm, "dup-name")
        assert first.status_code == 200, first.text
        second = _upload(client, {}, simple_add_wasm, "dup-name")
        assert second.status_code == 409, second.text


class TestOwnershipHelpersNoOracle:
    def test_foreign_module_id_resolves_to_404(self, client, test_db, simple_add_wasm, monkeypatch):

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)

        created = _upload(client, {}, simple_add_wasm, "owned-mod")
        assert created.status_code == 200, created.text
        module_id = created.json()["id"]

        stranger_id = uuid.uuid4()
        with pytest.raises(HTTPException) as exc_info:
            get_owned_wasm_module(test_db, module_id, stranger_id)
        assert exc_info.value.status_code == 404

        with pytest.raises(HTTPException) as exc_info:
            assert_wasm_ids_owned(test_db, [module_id], stranger_id)
        assert exc_info.value.status_code == 404

    def test_missing_and_foreign_ids_both_404(self, test_db):
        stranger_id = uuid.uuid4()
        with pytest.raises(HTTPException) as exc_info:
            get_owned_wasm_module(test_db, str(uuid.uuid4()), stranger_id)
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail.lower()


class TestSaveAndRunStartWiring:
    def test_extract_wasm_ids_from_yaml(self):
        from backend.routers.wasm import extract_wasm_ids_from_yaml

        yaml_text = (
            "pipeline:\n"
            "  name: p\n"
            "  steps:\n"
            "    - name: load_data\n"
            "      type: load\n"
            '      file_id: "11111111-1111-1111-1111-111111111111"\n'
            "    - name: scored\n"
            "      type: wasm_compute\n"
            '      wasm_file_id: "22222222-2222-2222-2222-222222222222"\n'
        )
        assert extract_wasm_ids_from_yaml(yaml_text) == {"22222222-2222-2222-2222-222222222222"}
        assert extract_wasm_ids_from_yaml("pipeline:\n  name: p\n") == set()

    def test_validate_pipeline_wasm_ownership_rejects_foreign(
        self, client, test_db, simple_add_wasm, monkeypatch
    ):

        from backend.routers.wasm import validate_pipeline_wasm_ownership

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)

        created = _upload(client, {}, simple_add_wasm, "wired-mod")
        assert created.status_code == 200, created.text
        module_id = created.json()["id"]

        yaml_text = (
            "pipeline:\n  name: p\n  steps:\n"
            "    - name: s\n      type: wasm_compute\n"
            f'      wasm_file_id: "{module_id}"\n'
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_pipeline_wasm_ownership(test_db, yaml_text, uuid.uuid4())
        assert exc_info.value.status_code == 404

    def test_load_owned_modules_for_run_rejects_foreign(
        self, client, test_db, simple_add_wasm, monkeypatch
    ):

        from backend.routers.wasm import load_owned_wasm_modules_for_run

        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)

        created = _upload(client, {}, simple_add_wasm, "run-mod")
        assert created.status_code == 200, created.text
        module_id = created.json()["id"]

        monkeypatch.setattr(
            "backend.routers.wasm.load_wasm_bytes_sync",
            lambda storage_key: simple_add_wasm,
        )
        loaded = load_owned_wasm_modules_for_run(
            test_db,
            [module_id],
            _owner_of(test_db, module_id),
        )
        assert loaded[module_id] == simple_add_wasm

        with pytest.raises(HTTPException) as exc_info:
            load_owned_wasm_modules_for_run(test_db, [module_id], uuid.uuid4())
        assert exc_info.value.status_code == 404


def _owner_of(test_db, module_id):
    from backend.models import WasmModule
    from backend.utils.uuid_utils import as_uuid

    row = test_db.query(WasmModule).filter(WasmModule.id == as_uuid(module_id)).first()
    return row.user_id
