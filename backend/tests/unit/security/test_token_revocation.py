"""Regression tests for JWT revocation fail-closed behavior (S26).

A revoked token must never be accepted, including when Redis is
unreachable. An unreachable revocation store must deny, not allow.
"""

from unittest.mock import patch

from backend.auth import create_access_token, revoke_token, verify_token


class _MemoryRedis:
    """Minimal dict-backed stand-in for the cache redis client."""

    def __init__(self):
        self.store: dict = {}

    def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value


def _valid_token() -> str:
    return create_access_token({"sub": "user-1", "role": "viewer"})


def test_valid_token_accepted_when_redis_up():
    redis = _MemoryRedis()
    with patch("backend.db.redis_pools.get_cache_redis", return_value=redis):
        payload = verify_token(_valid_token())
    assert payload is not None
    assert payload["sub"] == "user-1"


def test_revoked_token_rejected_when_redis_up():
    redis = _MemoryRedis()
    token = _valid_token()
    with patch("backend.db.redis_pools.get_cache_redis", return_value=redis):
        revoke_token(token)
        assert verify_token(token) is None


def test_revocation_check_redis_down_denies():
    """Redis outage must not turn revocation into an allow-list bypass."""
    token = _valid_token()

    def _boom():
        raise ConnectionError("redis down")

    with patch("backend.db.redis_pools.get_cache_redis", side_effect=_boom):
        assert verify_token(token) is None
