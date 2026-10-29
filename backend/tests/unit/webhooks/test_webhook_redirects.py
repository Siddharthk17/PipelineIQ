"""Regression tests for webhook redirect safety (S12).

The delivery client must never follow a redirect to a non-public URL.
Each redirect hop is re-validated; unsafe targets abort the delivery
without any further request.
"""

import httpx
import pytest

import backend.tasks.webhook_tasks as wt
from backend.utils.url_security import UnsafeURL


class FakeResponse:
    def __init__(self, status_code, location=None, text=""):
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.text = text


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def post(self, url, content=None, headers=None):
        self.calls.append(("POST", url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def get(self, url, headers=None):
        self.calls.append(("GET", url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _allow_only_start(monkeypatch):
    def fake_validate(url):
        if url.startswith("https://hooks.example.test/"):
            return url
        raise UnsafeURL(f"blocked: {url}")

    monkeypatch.setattr(wt, "validate_public_http_url", fake_validate)


def test_redirect_to_internal_host_aborts_without_follow(monkeypatch):
    """A 302 to a metadata/private address must not be fetched."""
    _allow_only_start(monkeypatch)
    client = FakeClient(
        [FakeResponse(302, location="http://169.254.169.254/latest/meta-data/")]
    )
    with pytest.raises(UnsafeURL):
        _run(client, "https://hooks.example.test/hook")
    assert client.calls == [("POST", "https://hooks.example.test/hook")]


def test_redirect_to_allowed_host_converts_post_to_get(monkeypatch):
    _allow_only_start(monkeypatch)
    client = FakeClient(
        [
            FakeResponse(302, location="https://hooks.example.test/v2/hook"),
            FakeResponse(200, text="ok"),
        ]
    )
    resp = _run(client, "https://hooks.example.test/hook")
    assert resp.status_code == 200
    assert client.calls == [
        ("POST", "https://hooks.example.test/hook"),
        ("GET", "https://hooks.example.test/v2/hook"),
    ]


def test_307_redirect_repeats_post_with_body(monkeypatch):
    _allow_only_start(monkeypatch)
    client = FakeClient(
        [
            FakeResponse(307, location="https://hooks.example.test/v2/hook"),
            FakeResponse(200, text="ok"),
        ]
    )
    resp = _run(client, "https://hooks.example.test/hook")
    assert resp.status_code == 200
    assert [m for m, _ in client.calls] == ["POST", "POST"]


def test_redirect_loop_aborts_after_max_hops(monkeypatch):
    _allow_only_start(monkeypatch)
    client = FakeClient(
        [FakeResponse(302, location="https://hooks.example.test/loop")] * 5
    )
    with pytest.raises(httpx.RequestError):
        _run(client, "https://hooks.example.test/loop")
    assert len(client.calls) == 4  # initial + 3 hops


def test_no_redirect_single_post(monkeypatch):
    _allow_only_start(monkeypatch)
    client = FakeClient([FakeResponse(200, text="ok")])
    resp = _run(client, "https://hooks.example.test/hook")
    assert resp.status_code == 200
    assert client.calls == [("POST", "https://hooks.example.test/hook")]


def _run(client, url):
    import asyncio

    return asyncio.run(
        wt._post_with_safe_redirects(
            client, url, content=b"{}", headers={"Content-Type": "x"}
        )
    )
