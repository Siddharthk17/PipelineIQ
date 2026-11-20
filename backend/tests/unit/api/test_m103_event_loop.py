"""Regression tests for M-103 (sync I/O must not stall the event loop).

Upload endpoints wrap sync Redis helpers and metadata parsing in
_bounded_to_thread: timeouts degrade to 503 instead of hanging the loop.
"""

import time

import pytest
from fastapi import HTTPException

from backend.api import files as files_module


@pytest.mark.asyncio
class TestBoundedToThread:
    async def test_success_returns_result(self):
        assert (
            await files_module._bounded_to_thread(
                lambda x: x * 2, 21, timeout=5.0, service="t"
            )
            == 42
        )

    async def test_timeout_becomes_503(self):
        def slow():
            time.sleep(30)

        with pytest.raises(HTTPException) as exc:
            await files_module._bounded_to_thread(
                slow, timeout=0.05, service="Upstream"
            )
        assert exc.value.status_code == 503

    async def test_inner_http_exception_passes_through(self):
        def bad():
            raise HTTPException(status_code=400, detail="nope")

        with pytest.raises(HTTPException) as exc:
            await files_module._bounded_to_thread(
                bad, timeout=5.0, service="t"
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
class TestSlowRedisDegrades:
    async def test_pending_lookup_timeout_is_bounded(
        self, auth_client, monkeypatch
    ):
        from unittest.mock import MagicMock

        slow_client = MagicMock()

        def hanging_get(_key):
            time.sleep(30)
            return None

        slow_client.get.side_effect = hanging_get
        monkeypatch.setattr(
            files_module, "get_cache_redis", lambda: slow_client
        )
        monkeypatch.setattr(files_module, "_REDIS_TIMEOUT_SECONDS", 0.05)

        start = time.monotonic()
        with pytest.raises(HTTPException) as exc:
            await files_module._bounded_to_thread(
                files_module._get_pending_upload,
                "some-id",
                timeout=files_module._REDIS_TIMEOUT_SECONDS,
                service="Upload state store",
            )
        elapsed = time.monotonic() - start
        assert exc.value.status_code == 503
        assert elapsed < 5, f"loop stalled for {elapsed:.1f}s"
