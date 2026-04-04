"""Unit tests for Redis role separation and shared pool behavior."""

from backend.config import settings
from backend.db import redis_pools


class TestRedisURLs:
    def test_broker_url_is_configured(self):
        assert settings.REDIS_BROKER_URL

    def test_pubsub_url_is_configured(self):
        assert settings.REDIS_PUBSUB_URL

    def test_cache_url_is_configured(self):
        assert settings.REDIS_CACHE_URL

    def test_yjs_url_is_configured(self):
        assert settings.REDIS_YJS_URL

    def test_broker_and_pubsub_roles_can_differ(self):
        assert isinstance(settings.REDIS_BROKER_URL, str)
        assert isinstance(settings.REDIS_PUBSUB_URL, str)


class TestConnectionPools:
    def test_pools_are_module_singletons(self):
        assert redis_pools._pubsub_pool is redis_pools._pubsub_pool
        assert redis_pools._cache_pool is redis_pools._cache_pool

    def test_get_pubsub_redis_uses_shared_pool(self):
        client = redis_pools.get_pubsub_redis()
        assert client.connection_pool is redis_pools._pubsub_pool

    def test_get_cache_redis_uses_shared_pool(self):
        client = redis_pools.get_cache_redis()
        assert client.connection_pool is redis_pools._cache_pool

    def test_get_broker_redis_uses_shared_pool(self):
        client = redis_pools.get_broker_redis()
        assert client.connection_pool is redis_pools._broker_pool

    def test_get_yjs_redis_uses_shared_pool(self):
        client = redis_pools.get_yjs_redis()
        assert client.connection_pool is redis_pools._yjs_pool

    def test_async_clients_use_async_pools(self):
        pubsub_client = redis_pools.get_pubsub_redis_async()
        cache_client = redis_pools.get_cache_redis_async()
        assert pubsub_client.connection_pool is redis_pools._pubsub_async_pool
        assert cache_client.connection_pool is redis_pools._cache_async_pool
