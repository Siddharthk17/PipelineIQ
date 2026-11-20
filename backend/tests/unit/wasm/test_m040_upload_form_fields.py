"""Regression tests for M-040: WASM upload must accept multipart form fields.

Bug: the frontend (frontend/lib/api.ts uploadWasmModule) sends name,
description and fuel_budget as multipart form fields, but the endpoint
declared them as query params. Every UI upload therefore 422'd.
Fix: accept multipart form fields, keeping query-param compat.
"""

from unittest.mock import MagicMock


def _mock_minio(monkeypatch):
    mock_client = MagicMock()
    mock_client.bucket_exists.return_value = True
    monkeypatch.setattr("backend.routers.wasm.get_minio_client", lambda: mock_client)
    return mock_client


def _upload_kwargs(name="mymod", description=None, fuel_budget=None):
    data = {"name": name}
    if description is not None:
        data["description"] = description
    if fuel_budget is not None:
        data["fuel_budget"] = str(fuel_budget)
    return data


class TestUploadMultipartFormFields:
    def test_form_fields_accepted(self, client, simple_add_wasm, monkeypatch, tmp_path):
        _mock_minio(monkeypatch)
        resp = client.post(
            "/api/v1/wasm/upload",
            files={"file": ("m.wasm", simple_add_wasm, "application/wasm")},
            data=_upload_kwargs(name="formmod", description="via form", fuel_budget=5000000),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["name"] == "formmod"
        assert body["description"] == "via form"
        assert body["fuel_budget"] == 5000000

    def test_query_params_still_accepted(self, client, simple_add_wasm, monkeypatch):
        _mock_minio(monkeypatch)
        resp = client.post(
            "/api/v1/wasm/upload?name=querymod",
            files={"file": ("m.wasm", simple_add_wasm, "application/wasm")},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "querymod"

    def test_missing_name_rejected(self, client, simple_add_wasm, monkeypatch):
        _mock_minio(monkeypatch)
        resp = client.post(
            "/api/v1/wasm/upload",
            files={"file": ("m.wasm", simple_add_wasm, "application/wasm")},
        )
        assert resp.status_code in (400, 422), resp.text

    def test_invalid_fuel_budget_rejected(self, client, simple_add_wasm, monkeypatch):
        _mock_minio(monkeypatch)
        resp = client.post(
            "/api/v1/wasm/upload",
            files={"file": ("m.wasm", simple_add_wasm, "application/wasm")},
            data=_upload_kwargs(name="badfuel", fuel_budget="not-a-number"),
        )
        assert resp.status_code in (400, 422), resp.text
