"""Redis caching utilities for PipelineIQ.

Provides simple get/set/delete operations with optional TTL.
Used to cache expensive lineage computations and stats.
"""

import json
import logging
from typing import Any, Optional

import redis

from backend.config import settings

logger = logging.getLogger(__name__)

redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)


def cache_get(key: str) -> Optional[Any]:
    """Get a cached value by key. Returns None on miss."""
    try:
        value = redis_client.get(key)
    except redis.RedisError:
        logger.warning("Redis cache_get failed for key: %s", key)
        return None
    if value is None:
        logger.debug("Cache MISS: %s", key)
        return None
    logger.debug("Cache HIT: %s", key)
    return json.loads(value)


def cache_set(key: str, value: Any, ttl: Optional[int] = None) -> None:
    """Set a cached value. If ttl is given (seconds), key expires after ttl."""
    try:
        serialized = json.dumps(value)
        if ttl:
            redis_client.setex(key, ttl, serialized)
        else:
            redis_client.set(key, serialized)
    except redis.RedisError:
        logger.warning("Redis cache_set failed for key: %s", key)


def cache_delete(key: str) -> None:
    """Delete a single cached key."""
    try:
        redis_client.delete(key)
    except redis.RedisError:
        logger.warning("Redis cache_delete failed for key: %s", key)


def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching a glob pattern using SCAN (non-blocking)."""
    try:
        cursor = 0
        while True:
            result = redis_client.scan(cursor=cursor, match=pattern, count=100)
            if not isinstance(result, (list, tuple)) or len(result) != 2:
                keys = redis_client.keys(pattern)
                if keys:
                    redis_client.delete(*keys)
                return
            cursor, keys = result
            if keys:
                redis_client.delete(*keys)
            if cursor == 0 or cursor == "0":
                break
    except redis.RedisError:
        logger.warning("Redis cache_delete_pattern failed for pattern: %s", pattern)
