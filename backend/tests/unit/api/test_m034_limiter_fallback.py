"""Regression tests for M-034: rate limiter must fail OPEN on Redis outage.

With the Redis backend unreachable, rate limiting must degrade to
per-process in-memory accounting — not raise (fail-closed), which turns
every rate-limited endpoint into a 500/503. Auth revocation stays
fail-closed (backend/auth.py); this fallback is rate limiting only.
"""

import pytest
from starlette.requests import Request

from backend.utils import rate_limiter as rate_limiter_module


def _request(path="/m034-probe"):
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
    }
    return Request(scope)


@pytest.fixture()
def dead_redis_limiter():
    """Production limiter with its storage pointed at a dead Redis."""
    from limits.storage import RedisStorage

    limiter = rate_limiter_module.limiter
    dead = RedisStorage("redis://127.0.0.1:6399/0")  # closed port: fast refuse
    old_storage, old_inner = limiter._storage, limiter._limiter.storage
    old_dead = limiter._storage_dead
    limiter._storage = dead
    limiter._limiter.storage = dead
    limiter._storage_dead = False
    try:
        yield limiter
    finally:
        limiter._storage = old_storage
        limiter._limiter.storage = old_inner
        limiter._storage_dead = old_dead


def test_in_memory_fallback_is_enabled():
    """The production limiter must opt into slowapi's memory fallback."""
    assert rate_limiter_module.limiter._in_memory_fallback_enabled is True


def test_rate_limit_fails_open_when_redis_down(dead_redis_limiter):
    """A dead Redis backend must not raise: memory fallback serves."""
    limiter = dead_redis_limiter

    @limiter.limit("5/minute")
    def _endpoint(request):
        return "ok"

    # First request trips the dead backend and engages the fallback;
    # subsequent requests are served from memory. None may raise.
    for _ in range(3):
        limiter._check_request_limit(_request(), _endpoint, False)


def test_rate_limit_still_enforced_from_memory(dead_redis_limiter):
    """Fallback must still rate-limit (per-process), not disable limits."""
    from slowapi.errors import RateLimitExceeded

    limiter = dead_redis_limiter

    @limiter.limit("2/minute")
    def _tight_endpoint(request):
        return "ok"

    limiter._check_request_limit(_request("/m034-tight"), _tight_endpoint, False)
    limiter._check_request_limit(_request("/m034-tight"), _tight_endpoint, False)
    with pytest.raises(RateLimitExceeded):
        limiter._check_request_limit(
            _request("/m034-tight"), _tight_endpoint, False)


def test_backend_status_check_reports_memory_when_redis_down(monkeypatch):
    """Startup self-check must report the fallback without raising."""
    monkeypatch.setattr(
        rate_limiter_module, "_redis_reachable", lambda *a, **k: False)
    assert rate_limiter_module.log_rate_limit_backend_status() == "memory"


def test_backend_status_check_reports_redis_when_reachable(monkeypatch):
    monkeypatch.setattr(
        rate_limiter_module, "_redis_reachable", lambda *a, **k: True)
    assert rate_limiter_module.log_rate_limit_backend_status() == "redis"
