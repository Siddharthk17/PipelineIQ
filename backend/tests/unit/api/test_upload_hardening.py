"""Permanent regression tests for W2 upload hardening.

Covers M-101 (malformed CSV 400 + no orphans), M-102 (size-cap
enforcement), M-038 (valid JSON shapes accepted), M-017 (bounded
preview of large files), M-066 (honest .xlsx rejection) and M-075
(formula neutralisation in previews). Each test was verified to FAIL
on the pre-fix code and pass after the fix.
"""

import os
import uuid
from pathlib import Path

import pytest

import backend.api.files as files_mod


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"hard_{suffix}@t.local",
            "username": f"hard_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"hard_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def uploader(auth_client):
    """Multipart uploader bound to a single freshly registered user."""

    class _Uploader:
        def __init__(self):
            self.client = auth_client
            self.headers = _register_login(auth_client)

        def go(self, filename, content, ctype="text/csv", extra_headers=None):
            boundary = "BND"
            body = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
            request_headers = {
                "Authorization": self.headers["Authorization"],
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            }
            if extra_headers:
                request_headers.update(extra_headers)
            return self.client.post(
                "/api/v1/files/upload", content=body, headers=request_headers
            )

    return _Uploader()


def _storage_files():
    base = files_mod.storage_service.provider.base_dir
    directory = base if os.path.isabs(base) else Path(os.getcwd()) / base
    return set(directory.iterdir()) if directory.exists() else set()


def test_m101_nul_csv_rejected_400_without_orphan(uploader):
    """M-101: NUL-byte CSV is malformed (400, not 201) and leaves no object."""
    before = _storage_files()
    resp = uploader.go("nul.csv", b"a,b\n1,\x00bad\n")
    assert resp.status_code == 400, resp.text
    assert _storage_files() == before


def test_m102_size_cap_enforced_early_and_streaming(uploader):
    """M-102: oversized declarations rejected early; over-cap bodies cut off."""
    # Forged huge Content-Length never reaches the body parser.
    resp = uploader.go(
        "s.csv", b"a,b\n1,2\n", extra_headers={"Content-Length": str(600 * 1024 * 1024)}
    )
    assert resp.status_code == 413, resp.text
    # A body past the cap aborts mid-stream and cleans the partial object.
    files_mod.settings.MAX_UPLOAD_SIZE = 1024
    before = _storage_files()
    resp = uploader.go("big.csv", b"a,b\n" + b"x,y\n" * 500)
    assert resp.status_code == 413, resp.text
    assert _storage_files() == before


def test_m038_valid_json_shapes_accepted_garbage_rejected(uploader):
    """M-038: pretty objects, >1MB arrays and JSONL are valid JSON."""
    resp = uploader.go("pretty.json", b'{\n  "a": 1,\n  "b": "x"\n}\n', "application/json")
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 1
    assert resp.json()["columns"] == ["a", "b"]

    row = b'{"id": 1, "value": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}'
    count = (1_200_000 // (len(row) + 1)) + 2
    big = b"[" + b",".join([row] * count) + b"]"
    assert len(big) > 1_048_576
    resp = uploader.go("big.json", big, "application/json")
    assert resp.status_code == 201, resp.text[:300]
    assert resp.json()["row_count"] == count

    resp = uploader.go("lines.json", b'{"a":1}\n{"a":2}\n', "application/json")
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 2

    resp = uploader.go("junk.json", b"<!DOCTYPE html><p>nope", "application/json")
    assert resp.status_code == 422, resp.text


def test_m017_large_csv_preview_is_row_capped_and_bounded(uploader, monkeypatch):
    """M-017: previewing a large CSV reads only a bounded prefix."""
    body = b"a,b\n" + b"hello,world\n" * 200_000  # ~2.4MB
    resp = uploader.go("large.csv", body)
    assert resp.status_code == 201, resp.text[:300]
    file_id = resp.json()["id"]

    real_download = files_mod.storage_service.download
    max_single = {"n": 0}

    def counting_download(path):
        handle = real_download(path)
        orig_read = handle.read

        def counting_read(size=-1):
            chunk = orig_read(size)
            max_single["n"] = max(max_single["n"], len(chunk))
            return chunk

        handle.read = counting_read
        return handle

    monkeypatch.setattr(files_mod.storage_service, "download", counting_download)
    preview = uploader.client.get(
        f"/api/v1/files/{file_id}/preview?rows=5", headers=uploader.headers
    )
    assert preview.status_code == 200, preview.text
    assert len(preview.json()["data"]) == 5
    assert max_single["n"] <= 1_048_576, f"single read {max_single['n']}"


def test_m066_xlsx_rejected_with_415(uploader):
    """M-066: .xlsx is honestly rejected (openpyxl not installed)."""
    files_mod.settings.ALLOWED_EXTENSIONS = {".csv", ".json", ".parquet", ".xlsx"}
    resp = uploader.go("book.xlsx", b"PK\x03\x04" + b"\x00" * 100, "application/vnd.ms-excel")
    assert resp.status_code == 415, resp.text
    assert "XLSX" in resp.text


def test_m075_preview_neutralises_formula_cells(uploader):
    """M-075: formula-looking preview cells are single-quote prefixed."""
    resp = uploader.go("f.csv", b"a,b\n=1+1,ok\n@x,y\n")
    assert resp.status_code == 201, resp.text
    file_id = resp.json()["id"]
    preview = uploader.client.get(
        f"/api/v1/files/{file_id}/preview", headers=uploader.headers
    )
    assert preview.status_code == 200, preview.text
    rows = preview.json()["data"]
    assert rows[0]["a"] == "'=1+1", rows
    assert rows[1]["a"] == "'@x", rows
