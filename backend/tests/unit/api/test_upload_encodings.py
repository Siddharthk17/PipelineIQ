"""Regression tests for upload text-encoding handling (B02).

Valid CSVs in common encodings must be accepted: UTF-8 (with and without
BOM), UTF-16 and Windows-1252/Latin-1. The BOM must not leak into the
first column name, and undecodable binary must still be rejected.
"""

import uuid

import pytest


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"b02_{suffix}@t.local",
            "username": f"b02_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"b02_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def uploader(auth_client):
    headers = _register_login(auth_client)

    def go(filename, content):
        boundary = "BND"
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return auth_client.post(
            "/api/v1/files/upload",
            content=body,
            headers={
                "Authorization": headers["Authorization"],
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )

    return go


def test_latin1_csv_accepted(uploader):
    resp = uploader("latin1.csv", "nom,ville\nAndré,Paris\n".encode("latin-1"))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["columns"] == ["nom", "ville"]
    assert body["row_count"] == 1


def test_utf16_csv_accepted(uploader):
    resp = uploader("utf16.csv", "a,b\n1,2\n".encode("utf-16"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["columns"] == ["a", "b"]


def test_bom_does_not_leak_into_column_name(uploader):
    resp = uploader("bom.csv", b"\xef\xbb\xbfa,b\n1,2\n")
    assert resp.status_code == 201, resp.text
    assert resp.json()["columns"] == ["a", "b"]


def test_binary_garbage_still_rejected(uploader):
    resp = uploader("junk.csv", bytes(range(256)))
    assert resp.status_code == 422, resp.text


def test_latin1_preview_matches_metadata(auth_client, uploader):
    headers = _register_login(auth_client)
    resp = auth_client.post(
        "/api/v1/files/upload",
        content=(
            b"--B\r\nContent-Disposition: form-data; name=\"file\"; "
            b'filename="p.csv"\r\nContent-Type: text/csv\r\n\r\n'
            + "nom,ville\nAndré,Paris\n".encode("latin-1")
            + b"\r\n--B--\r\n"
        ),
        headers={
            "Authorization": headers["Authorization"],
            "Content-Type": "multipart/form-data; boundary=B",
        },
    )
    file_id = resp.json()["id"]
    preview = auth_client.get(
        f"/api/v1/files/{file_id}/preview", headers=headers)
    assert preview.status_code == 200, preview.text
    assert preview.json()["columns"] == ["nom", "ville"]
