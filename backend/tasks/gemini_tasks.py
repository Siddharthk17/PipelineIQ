"""
All Gemini API calls go through this single Celery task.
Runs on the 'gemini' queue with concurrency=1 and rate_limit='50/m'.
This ensures we never exceed Gemini's free tier limits.
"""
import hashlib
import json
import random
import time
from typing import Any

import structlog
from celery.utils.log import get_task_logger

from backend.celery_app import celery_app
from backend.db.redis_pools import get_cache_redis

logger = get_task_logger(__name__)

# Token budget configuration
TOKEN_BUDGET_PER_MINUTE = 900_000
TOKEN_BUDGET_WINDOW_SECONDS = 60

# Cache TTL for responses: 1 hour
RESPONSE_CACHE_TTL = 3600


def _is_transient_server_error(error_str: str) -> bool:
    lowered = error_str.lower()
    return any(code in error_str for code in ["500", "503"]) or any(
        marker in lowered for marker in ["internal", "unavailable"]
    )


def _compute_retry_delay(
    retries: int,
    *,
    base_seconds: int = 5,
    cap_seconds: int = 60,
    jitter_seconds: int = 3,
) -> int:
    """Compute capped exponential backoff with bounded jitter."""
    delay = min(cap_seconds, base_seconds * (2 ** max(retries, 0)))
    return delay + random.randint(0, jitter_seconds)


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
    generation_config_overrides: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> str:
    """
    Single entry point for all Gemini API calls.
    """
    # Celery overrides root logger to WARNING; restore configured level
    # so structlog INFO messages are not silently dropped.
    import logging
    logging.getLogger().setLevel(logging.INFO)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        task_id=self.request.id,
        queue="gemini",
    )
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    slog = structlog.get_logger()

    # Step 1: Check response cache
    config_overrides = generation_config_overrides or {}
    cache_input = json.dumps(
        {
            "prompt": prompt,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "generation_config_overrides": config_overrides,
        },
        sort_keys=True,
        default=str,
    )
    cache_key = f"gemini:resp:{hashlib.sha256(cache_input.encode()).hexdigest()}"

    redis = get_cache_redis()
    cached = redis.get(cache_key)
    if cached:
        slog.info("gemini_cache_hit", cache_key_prefix=cache_key[:16])
        return cached

    # Step 2: Check token budget
    estimated_input_tokens = len(prompt) // 4
    estimated_total_tokens = estimated_input_tokens + max_output_tokens

    budget_key = f"gemini:tokens:{int(time.time() // TOKEN_BUDGET_WINDOW_SECONDS)}"
    current_usage = int(redis.get(budget_key) or 0)

    if current_usage + estimated_total_tokens > TOKEN_BUDGET_PER_MINUTE:
        backoff = _compute_retry_delay(self.request.retries)
        slog.warning(
            "gemini_token_budget_exhausted",
            current_usage=current_usage,
            estimated_total_tokens=estimated_total_tokens,
            retry_in_seconds=backoff,
        )
        raise self.retry(
            exc=Exception("Token budget exhausted"),
            countdown=backoff,
        )

    # Step 3: Call Gemini
    try:
        from backend.clients.gemini_client import get_gemini_model

        model = get_gemini_model()
        generation_config = {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        generation_config.update(config_overrides)
        response = model.generate_content(
            prompt,
            generation_config=generation_config,
        )

        result_text = response.text.strip()

        # Step 4: Update token budget
        actual_tokens = getattr(response.usage_metadata, "total_token_count",
                                estimated_total_tokens)
        redis.incrby(budget_key, actual_tokens)
        redis.expire(budget_key, TOKEN_BUDGET_WINDOW_SECONDS * 2)

        # Step 5: Cache the response
        redis.setex(cache_key, RESPONSE_CACHE_TTL, result_text)

        slog.info(
            "gemini_call_succeeded",
            actual_tokens=actual_tokens,
            response_length=len(result_text),
        )
        return result_text

    except Exception as e:
        error_str = str(e)

        if _is_free_tier_hard_quota(error_str):
            # Free tier exhausted - return ERROR indicator so UI can show message
            # Don't cache this - user needs to know quota is exhausted
            slog.warning("gemini_free_tier_quota_exhausted")
            return "GEMINI_QUOTA_EXHAUSTED"

        if _should_retry_rate_limit(error_str):
            backoff = _compute_retry_delay(self.request.retries)
            slog.warning(
                "gemini_rate_limited",
                attempt=self.request.retries + 1,
                max_retries=self.max_retries,
                retry_in_seconds=backoff,
            )
            raise self.retry(exc=e, countdown=backoff)

        if _is_transient_server_error(error_str):
            backoff = _compute_retry_delay(self.request.retries)
            slog.warning(
                "gemini_server_error",
                attempt=self.request.retries + 1,
                max_retries=self.max_retries,
                retry_in_seconds=backoff,
                error=error_str[:200],
            )
            raise self.retry(exc=e, countdown=backoff)

        slog.error("gemini_client_error", error=error_str[:500])
        return f"GEMINI_ERROR: {error_str[:500]}"
    finally:
        structlog.contextvars.clear_contextvars()
