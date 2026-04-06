"""Redis caching utilities for PipelineIQ.

Provides simple get/set/delete operations with optional TTL.
Used to cache expensive lineage computations and stats.
"""

import logging
from typing import Any, Optional

import orjson
import redis

from backend.db.redis_pools import get_cache_redis

logger = logging.getLogger(__name__)


def _get_client() -> redis.Redis:
    """Lazily create Redis client to avoid startup crashes."""
    return get_cache_redis()


def cache_get(key: str) -> Optional[Any]:
    """Get a cached value by key. Returns None on miss."""
    try:
        value = _get_client().get(key)
    except redis.RedisError:
        logger.warning("Redis cache_get failed for key: %s", key)
        return None
    if value is None:
        logger.debug("Cache MISS: %s", key)
        return None
    logger.debug("Cache HIT: %s", key)
    return orjson.loads(value)


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Set a cached value. If ttl is given (seconds), key expires after ttl."""
    try:
        serialized = orjson.dumps(value)
        client = _get_client()
        if ttl and ttl > 0:
            client.setex(key, ttl, serialized)
        else:
            client.set(key, serialized)
    except redis.RedisError:
        logger.warning("Redis cache_set failed for key: %s", key)


def cache_delete(key: str) -> None:
    """Delete a single cached key."""
    try:
        _get_client().delete(key)
    except redis.RedisError:
        logger.warning("Redis cache_delete failed for key: %s", key)


def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching a glob pattern using SCAN (non-blocking)."""
    try:
        client = _get_client()
        cursor = 0
        while True:
            result = client.scan(cursor=cursor, match=pattern, count=100)
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                logger.warning(
                    "Unexpected SCAN result for pattern '%s': %s", pattern, result
                )
                return
            cursor, keys = result
            if keys:
                client.delete(*keys)
            if cursor == 0 or cursor == "0":
                break
    except redis.RedisError:
        logger.warning("Redis cache_delete_pattern failed for pattern: %s", pattern)
