"""Unit tests for Redis role separation and shared pool behavior."""

from backend.config import settings
from backend.db import redis_pools


class TestRedisURLs:
    def test_broker_and_backend_use_different_urls(self):
        assert settings.REDIS_BROKER_URL
        assert settings.REDIS_BACKEND_URL
        assert settings.REDIS_BROKER_URL != settings.REDIS_BACKEND_URL

    def test_pubsub_url_is_separate_from_broker(self):
        assert settings.REDIS_PUBSUB_URL != settings.REDIS_BROKER_URL

    def test_cache_url_is_separate_from_pubsub(self):
        assert settings.REDIS_CACHE_URL != settings.REDIS_PUBSUB_URL

    def test_yjs_url_is_configured(self):
        assert settings.REDIS_YJS_URL


class TestConnectionPools:
    def test_pools_are_module_singletons(self):
        assert redis_pools._pubsub_pool is redis_pools._pubsub_pool
        assert redis_pools._cache_pool is redis_pools._cache_pool
        assert redis_pools._cache_binary_pool is redis_pools._cache_binary_pool

    def test_pool_sizes_match_week1_targets(self):
        assert redis_pools._pubsub_pool.max_connections >= 50
        assert redis_pools._cache_pool.max_connections >= 100
        assert redis_pools._cache_binary_pool.max_connections >= 100

    def test_get_pubsub_redis_uses_shared_pool(self):
        client = redis_pools.get_pubsub_redis()
        assert client.connection_pool is redis_pools._pubsub_pool

    def test_get_cache_redis_uses_shared_pool(self):
        client = redis_pools.get_cache_redis()
        assert client.connection_pool is redis_pools._cache_pool

    def test_get_cache_redis_binary_uses_shared_pool(self):
        client = redis_pools.get_cache_redis_binary()
        assert client.connection_pool is redis_pools._cache_binary_pool
        assert client.connection_pool.connection_kwargs["decode_responses"] is False

    def test_async_clients_use_async_pools(self):
        pubsub_client = redis_pools.get_pubsub_redis_async()
        cache_client = redis_pools.get_cache_redis_async()
        assert pubsub_client.connection_pool is redis_pools._pubsub_async_pool
        assert cache_client.connection_pool is redis_pools._cache_async_pool
