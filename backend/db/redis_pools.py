"""Shared Redis connection pools and role-specific client helpers."""

import ssl
from redis import ConnectionPool, Redis
from redis.asyncio import ConnectionPool as AsyncConnectionPool
from redis.asyncio import Redis as AsyncRedis

from backend.config import settings

def _pool_kwargs(url: str) -> dict:
    kwargs = {"decode_responses": True}
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
    return kwargs

_broker_pool = ConnectionPool.from_url(
    settings.REDIS_BROKER_URL,
    **_pool_kwargs(settings.REDIS_BROKER_URL),
)
_pubsub_pool = ConnectionPool.from_url(
    settings.REDIS_PUBSUB_URL,
    **_pool_kwargs(settings.REDIS_PUBSUB_URL),
)
_pubsub_async_pool = AsyncConnectionPool.from_url(
    settings.REDIS_PUBSUB_URL,
    **_pool_kwargs(settings.REDIS_PUBSUB_URL),
)
_cache_async_pool = AsyncConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    **_pool_kwargs(settings.REDIS_CACHE_URL),
)
_cache_pool = ConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    **_pool_kwargs(settings.REDIS_CACHE_URL),
)
_yjs_pool = ConnectionPool.from_url(
    settings.REDIS_YJS_URL,
    **_pool_kwargs(settings.REDIS_YJS_URL),
)

def get_broker_redis() -> Redis:
    """Return sync Redis client for broker-style operations."""
    return Redis(connection_pool=_broker_pool)

def get_pubsub_redis() -> Redis:
    """Return sync Redis client for pub/sub publish operations."""
    return Redis(connection_pool=_pubsub_pool)

def get_pubsub_redis_async() -> AsyncRedis:
    """Return async Redis client for pub/sub subscribe operations."""
    return AsyncRedis(connection_pool=_pubsub_async_pool)

def get_cache_redis() -> Redis:
    """Return sync Redis client for cache operations."""
    return Redis(connection_pool=_cache_pool)

def get_cache_redis_async() -> AsyncRedis:
    """Return async Redis client for cache operations."""
    return AsyncRedis(connection_pool=_cache_async_pool)

def get_yjs_redis() -> Redis:
    """Return sync Redis client for YJS operations."""
    return Redis(connection_pool=_yjs_pool)
