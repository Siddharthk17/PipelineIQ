"""
Redis cache for parsed pipeline objects.

Why this matters:
  PyYAML parsing a 200-line pipeline takes ~5ms.
  At 500 run submissions per minute, that is 2.5 CPU-seconds/minute just parsing.
  The same YAML always parses to the same object.
  Cache it: SHA256 → pickle of parsed PipelineConfig object.

Cache key: SHA256 of the raw YAML string.
  - Same YAML content = same hash = same cached result
  - Single byte change = different hash = fresh parse
  - This is safe because YAML parsing is deterministic

Cache backend: redis-cache (not redis-broker)
  - Parsed pipelines are app cache, not task state
  - TTL: 1 hour (more than enough for any pipeline session)
"""
import hashlib
import logging
import os
import pickle
from urllib.parse import urlparse
from collections import OrderedDict

from backend.config import settings
from backend.db.redis_pools import get_cache_redis
from backend.pipeline.parser import PipelineParser

logger = logging.getLogger(__name__)

YAML_CACHE_TTL = 3600  # 1 hour in seconds
YAML_CACHE_PREFIX = "yaml:parsed:"
LOCAL_CACHE_MAX_ENTRIES = 256
_DOCKER_INTERNAL_REDIS_HOSTS = {"redis-cache", "redis-broker", "redis-pubsub", "redis-yjs"}

# Module-level parser instance — stateless, safe to reuse
_parser = PipelineParser()
_local_parsed_cache: "OrderedDict[str, object]" = OrderedDict()
_redis_cache_disabled = False


def _local_cache_get(cache_key: str):
    cached = _local_parsed_cache.get(cache_key)
    if cached is None:
        return None
    _local_parsed_cache.move_to_end(cache_key)
    return cached


def _local_cache_set(cache_key: str, pipeline_obj: object) -> None:
    _local_parsed_cache[cache_key] = pipeline_obj
    _local_parsed_cache.move_to_end(cache_key)
    while len(_local_parsed_cache) > LOCAL_CACHE_MAX_ENTRIES:
        _local_parsed_cache.popitem(last=False)


def _should_use_redis_cache() -> bool:
    """Skip Redis cache when running outside Docker with Docker-only hostnames."""
    # Unit tests monkeypatch get_cache_redis() to in-memory fakes; keep Redis path enabled.
    if getattr(get_cache_redis, "__module__", "") != "backend.db.redis_pools":
        return True

    parsed = urlparse(settings.REDIS_CACHE_URL or "")
    host = parsed.hostname or ""
    if host in _DOCKER_INTERNAL_REDIS_HOSTS and not os.path.exists("/.dockerenv"):
        return False
    return True


def _redis_call(redis_client, operation: str, *args, **kwargs):
    global _redis_cache_disabled
    if _redis_cache_disabled:
        return None
    try:
        return getattr(redis_client, operation)(*args, **kwargs)
    except Exception as exc:
        _redis_cache_disabled = True
        logger.warning(
            "YAML cache %s failed; disabling Redis cache for this process: %s",
            operation,
            exc,
        )
        return None


def get_parsed_pipeline(yaml_text: str):
    """
    Get a parsed pipeline object, using Redis cache to avoid re-parsing.

    Args:
        yaml_text: Raw YAML string containing the pipeline definition

    Returns:
        PipelineConfig (same type as PipelineParser().parse() returns)

    Raises:
        InvalidYAMLError: If the YAML cannot be parsed
        MissingRequiredFieldError: If required fields are missing
        ValueError: If yaml_text is empty
    """
    if not yaml_text or not yaml_text.strip():
        raise ValueError("Pipeline YAML must not be empty")

    # SHA256 of the raw YAML — deterministic, collision-resistant
    yaml_hash = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
    cache_key = f"{YAML_CACHE_PREFIX}{yaml_hash}"

    local_cached = _local_cache_get(cache_key)
    if local_cached is not None:
        return local_cached

    if _should_use_redis_cache():
        redis = get_cache_redis()

        # Check cache first
        cached = _redis_call(redis, "get", cache_key)
        if cached:
            try:
                # redis pool returns str (decode_responses=True); pickle needs bytes
                if isinstance(cached, str):
                    cached = cached.encode("latin-1")
                pipeline = pickle.loads(cached)
                _local_cache_set(cache_key, pipeline)
                logger.debug(f"YAML cache HIT: {cache_key[:24]}...")
                return pipeline
            except Exception as e:
                logger.warning(f"YAML cache decode failed: {e}. Parsing fresh.")

    # Cache miss — parse the YAML (~5ms)
    pipeline = _parser.parse(yaml_text)
    _local_cache_set(cache_key, pipeline)

    # Store in cache for future calls
    if _should_use_redis_cache():
        redis = get_cache_redis()
        try:
            pickled = pickle.dumps(pipeline)
            # Store raw bytes — use the connection without decode_responses
            # Since pool uses decode_responses=True, we encode to latin-1 which
            # is a lossless round-trip for binary pickle data
            stored = _redis_call(
                redis, "set", cache_key, pickled.decode("latin-1"), ex=YAML_CACHE_TTL
            )
            if stored:
                logger.debug(f"YAML cache MISS → stored: {cache_key[:24]}...")
        except Exception as e:
            # Cache write failure is non-fatal — pipeline was parsed successfully
            logger.warning(f"YAML cache write failed: {e}")

    return pipeline


def invalidate_pipeline_cache(yaml_text: str) -> None:
    """
    Invalidate the cache for a specific YAML string.
    Call this when a pipeline definition is mutated.
    """
    yaml_hash = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()
    cache_key = f"{YAML_CACHE_PREFIX}{yaml_hash}"
    _local_parsed_cache.pop(cache_key, None)
    if _should_use_redis_cache():
        redis = get_cache_redis()
        deleted = _redis_call(redis, "delete", cache_key)
        if deleted is not None:
            logger.debug(f"YAML cache invalidated: {cache_key[:24]}...")
