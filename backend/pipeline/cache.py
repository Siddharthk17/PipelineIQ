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
import pickle

from backend.db.redis_pools import get_cache_redis
from backend.pipeline.parser import PipelineParser

logger = logging.getLogger(__name__)

YAML_CACHE_TTL = 3600  # 1 hour in seconds
YAML_CACHE_PREFIX = "yaml:parsed:"

# Module-level parser instance — stateless, safe to reuse
_parser = PipelineParser()


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

    redis = get_cache_redis()

    # Check cache first
    try:
        cached = redis.get(cache_key)
        if cached:
            # redis pool returns str (decode_responses=True); pickle needs bytes
            if isinstance(cached, str):
                cached = cached.encode("latin-1")
            pipeline = pickle.loads(cached)
            logger.debug(f"YAML cache HIT: {cache_key[:24]}...")
            return pipeline
    except Exception as e:
        # Cache read failure is non-fatal — fall through to parse
        logger.warning(f"YAML cache read failed: {e}. Parsing fresh.")

    # Cache miss — parse the YAML (~5ms)
    pipeline = _parser.parse(yaml_text)

    # Store in cache for future calls
    try:
        pickled = pickle.dumps(pipeline)
        # Store raw bytes — use the connection without decode_responses
        # Since pool uses decode_responses=True, we encode to latin-1 which
        # is a lossless round-trip for binary pickle data
        redis.set(cache_key, pickled.decode("latin-1"), ex=YAML_CACHE_TTL)
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
    redis = get_cache_redis()
    try:
        redis.delete(cache_key)
        logger.debug(f"YAML cache invalidated: {cache_key[:24]}...")
    except Exception as e:
        logger.warning(f"YAML cache invalidation failed: {e}")
