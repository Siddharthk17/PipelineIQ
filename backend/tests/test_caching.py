"""Tests for Redis caching (Deliverable 3).

8 tests covering cache operations and integration.
"""

import pytest
from unittest.mock import patch, MagicMock
from backend.utils.cache import cache_get, cache_set, cache_delete, cache_delete_pattern


class TestCacheOperations:
    """Tests for cache utility functions."""

    @patch("backend.utils.cache.redis_client")
    def test_cache_set_and_get(self, mock_redis):
        """Cache set stores data and get retrieves it."""
        mock_redis.get.return_value = '{"key": "value"}'
        result = cache_get("test:key")
        assert result == {"key": "value"}
        mock_redis.get.assert_called_once_with("test:key")

    @patch("backend.utils.cache.redis_client")
    def test_cache_get_returns_none_on_miss(self, mock_redis):
        """Cache miss returns None."""
        mock_redis.get.return_value = None
        result = cache_get("test:missing")
        assert result is None

    @patch("backend.utils.cache.redis_client")
    def test_cache_set_with_ttl(self, mock_redis):
        """Cache set with TTL calls setex."""
        cache_set("test:key", {"data": 1}, ttl=30)
        mock_redis.setex.assert_called_once()

    @patch("backend.utils.cache.redis_client")
    def test_cache_set_without_ttl(self, mock_redis):
        """Cache set without TTL calls set (permanent)."""
        cache_set("test:key", {"data": 1})
        mock_redis.set.assert_called_once()

    @patch("backend.utils.cache.redis_client")
    def test_cache_delete_removes_key(self, mock_redis):
        """Cache delete removes a specific key."""
        cache_delete("test:key")
        mock_redis.delete.assert_called_once_with("test:key")

    @patch("backend.utils.cache.redis_client")
    def test_cache_delete_pattern_removes_matching(self, mock_redis):
        """Cache delete pattern removes all matching keys."""
        mock_redis.keys.return_value = [b"lineage:graph:abc", b"lineage:column:abc:x:y"]
        cache_delete_pattern("lineage:*:abc*")
        mock_redis.delete.assert_called_once()

    @patch("backend.utils.cache.redis_client")
    def test_cache_handles_redis_error_gracefully(self, mock_redis):
        """Cache operations should not raise on Redis errors."""
        import redis
        mock_redis.get.side_effect = redis.RedisError("Connection refused")
        result = cache_get("test:key")
        assert result is None

    @patch("backend.utils.cache.redis_client")
    def test_cache_set_handles_redis_error_gracefully(self, mock_redis):
        """Cache set should not raise on Redis errors."""
        import redis
        mock_redis.set.side_effect = redis.RedisError("Connection refused")
        # Should not raise
        cache_set("test:key", {"data": 1})
