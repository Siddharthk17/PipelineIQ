"""Tests for Gemini rate-limit retry policy."""

from backend.tasks.gemini_tasks import _should_retry_rate_limit


class TestGeminiRateLimitPolicy:
    def test_does_not_retry_when_free_tier_limit_is_zero(self):
        error_message = (
            "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
            "generativelanguage.googleapis.com/generate_content_free_tier_requests, "
            "limit: 0, model: gemini-2.0-flash"
        )
        assert _should_retry_rate_limit(error_message) is False

    def test_retries_on_transient_quota_exhaustion(self):
        error_message = "429 RESOURCE_EXHAUSTED: quota exceeded, retry later"
        assert _should_retry_rate_limit(error_message) is True
