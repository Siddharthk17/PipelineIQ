"""
All Gemini API calls go through this single Celery task.
Runs on the 'gemini' queue with concurrency=1 and rate_limit='50/m'.
This ensures we never exceed Gemini's free tier limits.
"""
import hashlib
import logging
import time

import orjson
from celery.utils.log import get_task_logger

from backend.celery_app import celery_app
from backend.clients.gemini_client import get_gemini_model
from backend.db.redis_pools import get_cache_redis

logger = get_task_logger(__name__)

# Token budget configuration
TOKEN_BUDGET_PER_MINUTE = 900_000
TOKEN_BUDGET_WINDOW_SECONDS = 60

# Cache TTL for responses: 1 hour
RESPONSE_CACHE_TTL = 3600


def _is_free_tier_hard_quota(error_str: str) -> bool:
    """True when Google reports free-tier request quota is effectively disabled."""
    lowered = error_str.lower()
    return (
        "generate_content_free_tier_requests" in lowered
        and "limit: 0" in lowered
    )


def _should_retry_rate_limit(error_str: str) -> bool:
    """Retry only for transient throttling, not hard free-tier quota exhaustion."""
    if _is_free_tier_hard_quota(error_str):
        return False
    lowered = error_str.lower()
    return (
        "429" in error_str
        or "resource_exhausted" in lowered
        or "quota" in lowered
    )


@celery_app.task(
    name="tasks.call_gemini",
    queue="gemini",
    bind=True,
    max_retries=5,
    rate_limit="50/m",
    soft_time_limit=120,
    time_limit=150,
)
def call_gemini_task(
    self,
    prompt: str,
    temperature: float = 0.1,
    max_output_tokens: int = 2000,
) -> str:
    """
    Single entry point for all Gemini API calls.
    """
    # ── Step 1: Check response cache ─────────────────────────────────────
    cache_input = f"{prompt}|temp={temperature}|max={max_output_tokens}"
    cache_key = f"gemini:resp:{hashlib.sha256(cache_input.encode()).hexdigest()}"

    redis = get_cache_redis()
    cached = redis.get(cache_key)
    if cached:
        logger.info(f"Gemini cache HIT for key {cache_key[:16]}...")
        return cached

    # ── Step 2: Check token budget ────────────────────────────────────────
    estimated_input_tokens = len(prompt) // 4
    estimated_total_tokens = estimated_input_tokens + max_output_tokens

    budget_key = f"gemini:tokens:{int(time.time() // TOKEN_BUDGET_WINDOW_SECONDS)}"
    current_usage = int(redis.get(budget_key) or 0)

    if current_usage + estimated_total_tokens > TOKEN_BUDGET_PER_MINUTE:
        logger.warning(
            f"Gemini token budget exhausted "
            f"(used={current_usage}, needed={estimated_total_tokens}). "
            f"Retrying in 15s."
        )
        raise self.retry(
            exc=Exception("Token budget exhausted"),
            countdown=15,
        )

    # ── Step 3: Call Gemini ───────────────────────────────────────────────
    try:
        model = get_gemini_model()
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": temperature,
                "max_output_tokens": max_output_tokens,
            },
        )

        result_text = response.text.strip()

        # ── Step 4: Update token budget ───────────────────────────────────
        actual_tokens = getattr(response.usage_metadata, "total_token_count",
                                 estimated_total_tokens)
        redis.incrby(budget_key, actual_tokens)
        redis.expire(budget_key, TOKEN_BUDGET_WINDOW_SECONDS * 2)

        # ── Step 5: Cache the response ────────────────────────────────────
        redis.setex(cache_key, RESPONSE_CACHE_TTL, result_text)

        logger.info(
            f"Gemini call successful. "
            f"Tokens used: ~{actual_tokens}. "
            f"Response length: {len(result_text)} chars."
        )
        return result_text

    except Exception as e:
        error_str = str(e)

        if _is_free_tier_hard_quota(error_str):
            message = (
                "Gemini free-tier quota is unavailable for this API key/region (limit: 0). "
                "Use a supported region/key or wait for quota reset."
            )
            logger.error(message)
            raise RuntimeError(message) from e

        if _should_retry_rate_limit(error_str):
            backoff = 10 * (2 ** self.request.retries)
            logger.warning(
                f"Gemini rate limited (attempt {self.request.retries + 1}/5). "
                f"Retrying in {backoff}s."
            )
            raise self.retry(exc=e, countdown=backoff)

        if any(code in error_str for code in ["500", "503", "INTERNAL", "UNAVAILABLE"]):
            logger.warning(f"Gemini server error: {error_str[:200]}. Retrying in 30s.")
            raise self.retry(exc=e, countdown=30)

        logger.error(f"Gemini client error (not retrying): {error_str[:500]}")
        raise
