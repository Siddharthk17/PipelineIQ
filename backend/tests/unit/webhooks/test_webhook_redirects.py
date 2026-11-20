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


class TestS2003SyncPinning:
    """S2-003: sync webhook/Slack delivery pins DNS + fetches no redirects."""

    def test_sync_delivery_posts_to_pinned_ip_with_host(
            self, monkeypatch):
        import backend.services.webhook_service as ws
        from backend.models import Webhook

        calls = {}

        class FakeResp:
            status_code = 200
            text = "ok"

        class FakeClient:
            def __init__(self, *a, **k):
                calls["client_kw"] = k

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, content=None, headers=None, extensions=None):
                calls.update(
                    url=url, headers=headers, extensions=extensions)
                return FakeResp()

        monkeypatch.setattr(
            "backend.services.webhook_service.pin_url_to_validated_ip",
            lambda url: ("https://93.184.216.34/hook", "hooks.example.test", {}),
        )
        monkeypatch.setattr(
            "backend.services.webhook_service.validate_public_http_url",
            lambda url: url,
        )
        import httpx as _httpx

        monkeypatch.setattr(_httpx, "Client", FakeClient)
        import uuid as uuid_module
        hook = Webhook(
            id=uuid_module.uuid4(), user_id=uuid_module.uuid4(),
            url="https://hooks.example.test/hook", events=["x"])
        import datetime as dt
        from unittest.mock import MagicMock
        db = MagicMock()
        ws.deliver_webhook_sync(hook, "ping", {"data": {}}, db=db)
        assert calls["url"] == "https://93.184.216.34/hook"
        assert calls["headers"]["Host"] == "hooks.example.test"
        assert calls["client_kw"].get("follow_redirects") is False

    def test_slack_posts_pinned_without_redirects(self, monkeypatch):
        import httpx as real_httpx

        import backend.services.notification_service as ns

        calls = {}

        class FakeResp:
            status_code = 200
            text = "ok"

        def fake_post(url, json=None, headers=None, timeout=None,
                      follow_redirects=None):
            calls.update(
                url=url, headers=headers, follow_redirects=follow_redirects)
            return FakeResp()

        monkeypatch.setattr(
            "backend.services.notification_service.pin_url_to_validated_ip",
            lambda url: ("https://1.2.3.4/services/AAA", "hooks.slack.com", {}),
        )
        monkeypatch.setattr(
            "backend.services.notification_service.validate_public_http_url",
            lambda url: url,
        )
        monkeypatch.setattr(real_httpx, "post", fake_post)
        assert ns.send_slack_notification(
            "https://hooks.slack.com/services/AAA", "hi") is True
        assert calls["url"] == "https://1.2.3.4/services/AAA"
        assert calls["headers"] == {"Host": "hooks.slack.com"}
        assert calls["follow_redirects"] is False
