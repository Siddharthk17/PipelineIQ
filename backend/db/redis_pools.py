"""Shared Redis connection pools and role-specific client helpers."""

import ssl

from redis import ConnectionPool, Redis
from redis.asyncio import ConnectionPool as AsyncConnectionPool
from redis.asyncio import Redis as AsyncRedis

from backend.config import settings

_SOCKET_TIMEOUT_SECONDS = 5
_SOCKET_CONNECT_TIMEOUT_SECONDS = 5
_HEALTH_CHECK_INTERVAL_SECONDS = 30


def _pool_kwargs(
    url: str,
    *,
    decode_responses: bool,
    max_connections: int,
) -> dict:
    kwargs = {
        "decode_responses": decode_responses,
        "max_connections": max_connections,
        "socket_timeout": _SOCKET_TIMEOUT_SECONDS,
        "socket_connect_timeout": _SOCKET_CONNECT_TIMEOUT_SECONDS,
        "retry_on_timeout": True,
        "health_check_interval": _HEALTH_CHECK_INTERVAL_SECONDS,
    }
    if url.startswith("rediss://"):
        kwargs["ssl_cert_reqs"] = ssl.CERT_NONE
    return kwargs


_broker_pool = ConnectionPool.from_url(
    settings.REDIS_BROKER_URL,
    **_pool_kwargs(
        settings.REDIS_BROKER_URL,
        decode_responses=True,
        max_connections=25,
    ),
)
_pubsub_pool = ConnectionPool.from_url(
    settings.REDIS_PUBSUB_URL,
    **_pool_kwargs(
        settings.REDIS_PUBSUB_URL,
        decode_responses=True,
        max_connections=50,
    ),
)
_pubsub_async_pool = AsyncConnectionPool.from_url(
    settings.REDIS_PUBSUB_URL,
    **_pool_kwargs(
        settings.REDIS_PUBSUB_URL,
        decode_responses=True,
        max_connections=50,
    ),
)
_cache_pool = ConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    **_pool_kwargs(
        settings.REDIS_CACHE_URL,
        decode_responses=True,
        max_connections=100,
    ),
)
_cache_binary_pool = ConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    **_pool_kwargs(
        settings.REDIS_CACHE_URL,
        decode_responses=False,
        max_connections=100,
    ),
)
_cache_async_pool = AsyncConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    **_pool_kwargs(
        settings.REDIS_CACHE_URL,
        decode_responses=True,
        max_connections=100,
    ),
)
_yjs_pool = ConnectionPool.from_url(
    settings.REDIS_YJS_URL,
    **_pool_kwargs(
        settings.REDIS_YJS_URL,
        decode_responses=True,
        max_connections=20,
    ),
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


def get_cache_redis_binary() -> Redis:
    """Return sync Redis client for cache operations that require raw bytes."""
    return Redis(connection_pool=_cache_binary_pool)


def get_cache_redis_async() -> AsyncRedis:
    """Return async Redis client for cache operations."""
    return AsyncRedis(connection_pool=_cache_async_pool)


def get_yjs_redis() -> Redis:
    """Return sync Redis client for YJS operations."""
    return Redis(connection_pool=_yjs_pool)
