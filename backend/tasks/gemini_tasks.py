"""
All Gemini API calls go through this single Celery task.
Runs on the 'gemini' queue with concurrency=1 and rate_limit='50/m'.
This ensures we never exceed Gemini's free tier limits.
"""
import hashlib
import orjson
import random
import time
from typing import Any

import structlog
from celery.utils.log import get_task_logger

from backend.ai.redaction import clamp_prompt
from backend.celery_app import celery_app
from backend.db.redis_pools import get_cache_redis

logger = get_task_logger(__name__)


class GeminiQuotaExhaustedError(Exception):
    """Raised when Google reports the free-tier Gemini quota is hard-exhausted.

    This is a terminal failure — retries will not recover from a hard quota
    limit set to 0. Marking the task as a real failure prevents the
    "false success" pattern where the task returns a sentinel string
    that the Celery worker then mis-reports as `succeeded`.
    """


class GeminiClientError(Exception):
    """Raised when Gemini returns a non-retryable client error.

    The original error message is preserved so callers can surface the
    underlying cause to the user.
    """

# Token budget configuration
TOKEN_BUDGET_PER_MINUTE = 900_000
TOKEN_BUDGET_WINDOW_SECONDS = 60

# Cache TTL for responses: 1 hour
RESPONSE_CACHE_TTL = 3600
_TOKEN_BUDGET_LUA = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local requested = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if current + requested > limit then
  return -1
end
local updated = redis.call('INCRBY', KEYS[1], requested)
redis.call('EXPIRE', KEYS[1], ttl)
return updated
"""


def _reserve_token_budget(task, redis, estimated_total_tokens: int, slog) -> str:
    budget_key = f"gemini:tokens:{int(time.time() // TOKEN_BUDGET_WINDOW_SECONDS)}"
    reserved_usage = int(redis.eval(
        _TOKEN_BUDGET_LUA,
        1,
        budget_key,
        estimated_total_tokens,
        TOKEN_BUDGET_PER_MINUTE,
        TOKEN_BUDGET_WINDOW_SECONDS * 2,
    ))

    if reserved_usage < 0:
        backoff = _compute_retry_delay(task.request.retries)
        slog.warning(
            "gemini_token_budget_exhausted",
            current_usage=int(redis.get(budget_key) or 0),
            estimated_total_tokens=estimated_total_tokens,
            retry_in_seconds=backoff,
        )
        raise task.retry(
            exc=Exception("Token budget exhausted"),
            countdown=backoff,
        )
    return budget_key


def _release_token_budget(redis, budget_key: str, estimated_total_tokens: int) -> None:
    redis.incrby(budget_key, -estimated_total_tokens)
    redis.expire(budget_key, TOKEN_BUDGET_WINDOW_SECONDS * 2)


def _adjust_token_budget(
    redis,
    budget_key: str,
    actual_tokens: int,
    estimated_total_tokens: int,
) -> None:
    adjustment = int(actual_tokens) - estimated_total_tokens
    if adjustment:
        redis.incrby(budget_key, adjustment)
    redis.expire(budget_key, TOKEN_BUDGET_WINDOW_SECONDS * 2)


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
    tenant_id: str | None = None,
) -> str:
    """
    Single entry point for all Gemini API calls.
    """
    prompt = clamp_prompt(prompt)
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
    cache_input = orjson.dumps(
        {
            "tenant_id": tenant_id or "global",
            "prompt": prompt,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "generation_config_overrides": config_overrides,
        },
        option=orjson.OPT_SORT_KEYS,
    ).decode()
    cache_key = f"gemini:resp:{hashlib.sha256(cache_input.encode()).hexdigest()}"

    redis = get_cache_redis()
    cached = redis.get(cache_key)
    if cached:
        slog.info("gemini_cache_hit", cache_key_prefix=cache_key[:16])
        return cached

    # Step 2: Check token budget
    estimated_input_tokens = len(prompt) // 4
    estimated_total_tokens = estimated_input_tokens + max_output_tokens

    budget_reserved = False
    budget_key = _reserve_token_budget(self, redis, estimated_total_tokens, slog)
    budget_reserved = True

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
        usage_metadata = getattr(response, "usage_metadata", None)
        actual_tokens = getattr(
            usage_metadata,
            "total_token_count",
            estimated_total_tokens,
        )
        _adjust_token_budget(
            redis,
            budget_key,
            actual_tokens,
            estimated_total_tokens,
        )

        # Step 5: Cache the response
        redis.setex(cache_key, RESPONSE_CACHE_TTL, result_text)

        slog.info(
            "gemini_call_succeeded",
            actual_tokens=actual_tokens,
            response_length=len(result_text),
        )
        return result_text

    except Exception as e:
        if budget_reserved:
            _release_token_budget(redis, budget_key, estimated_total_tokens)
        error_str = str(e)

        if _is_free_tier_hard_quota(error_str):
            # Free tier exhausted - this is a TERMINAL failure, not a success.
            # Raising (instead of returning a sentinel string) ensures Celery
            # marks the task as FAILED rather than mis-reporting it as
            # succeeded with a poisoned payload. The state update surfaces
            # the reason to result consumers without putting a sentinel
            # in the result backend.
            slog.warning("gemini_free_tier_quota_exhausted")
            self.update_state(
                state="FAILURE",
                meta={
                    "error": "gemini_free_tier_quota_exhausted",
                    "reason": (
                        "Google Gemini free-tier quota exhausted. "
                        "Retries will not recover from a hard quota limit."
                    ),
                },
            )
            raise GeminiQuotaExhaustedError(
                "Google Gemini free-tier quota exhausted."
            ) from e

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

        # Non-retryable client error: raise a real exception so Celery marks
        # the task as FAILED instead of returning a poisoned sentinel.
        slog.error("gemini_client_error", error=error_str[:500])
        self.update_state(
            state="FAILURE",
            meta={"error": "gemini_client_error", "reason": error_str[:500]},
        )
        raise GeminiClientError(f"Gemini AI service error: {error_str[:500]}") from e
    finally:
        structlog.contextvars.clear_contextvars()


@celery_app.task(
    name="tasks.generate_pipeline_description",
    queue="gemini",
    bind=True,
    max_retries=3,
    rate_limit="50/m",
    soft_time_limit=60,
    time_limit=90,
)
def generate_pipeline_description_task(
    self,
    cache_key: str,
    prompt: str,
    tenant_id: str | None = None,
) -> str:
    """Generate and cache a short catalog description without blocking API threads."""
    prompt = clamp_prompt(prompt)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        task_id=self.request.id,
        queue="gemini",
        tenant_id=tenant_id or "global",
        purpose="pipeline_description",
    )
    slog = structlog.get_logger()
    redis = get_cache_redis()

    cache_input = orjson.dumps(
        {
            "tenant_id": tenant_id or "global",
            "prompt": prompt,
            "temperature": 0.1,
            "max_output_tokens": 100,
            "purpose": "pipeline_description",
        },
        option=orjson.OPT_SORT_KEYS,
    ).decode()
    response_cache_key = (
        f"gemini:resp:{hashlib.sha256(cache_input.encode()).hexdigest()}"
    )

    cached = redis.get(response_cache_key)
    if cached:
        result_text = cached.decode("utf-8") if isinstance(cached, bytes) else cached
        redis.setex(cache_key, RESPONSE_CACHE_TTL * 24, result_text)
        return result_text

    estimated_total_tokens = len(prompt) // 4 + 100
    budget_reserved = False
    budget_key = _reserve_token_budget(self, redis, estimated_total_tokens, slog)
    budget_reserved = True

    try:
        from backend.clients.gemini_client import get_gemini_model

        model = get_gemini_model()
        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.1,
                "max_output_tokens": 100,
            },
        )
        result_text = response.text.strip()
        usage_metadata = getattr(response, "usage_metadata", None)
        actual_tokens = getattr(
            usage_metadata,
            "total_token_count",
            estimated_total_tokens,
        )
        _adjust_token_budget(
            redis,
            budget_key,
            actual_tokens,
            estimated_total_tokens,
        )
        if result_text:
            redis.setex(response_cache_key, RESPONSE_CACHE_TTL, result_text)
            redis.setex(cache_key, RESPONSE_CACHE_TTL * 24, result_text)
        return result_text

    except Exception as exc:
        if budget_reserved:
            _release_token_budget(redis, budget_key, estimated_total_tokens)
        error_str = str(exc)
        if _should_retry_rate_limit(error_str) or _is_transient_server_error(error_str):
            backoff = _compute_retry_delay(self.request.retries)
            slog.warning(
                "pipeline_description_retry",
                retry_in_seconds=backoff,
                error=error_str[:200],
            )
            raise self.retry(exc=exc, countdown=backoff)
        slog.warning("pipeline_description_failed", error=error_str[:200])
        return ""
    finally:
        structlog.contextvars.clear_contextvars()
