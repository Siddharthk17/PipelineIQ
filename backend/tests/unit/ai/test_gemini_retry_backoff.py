"""Tests for Gemini transient retry backoff behavior."""

from backend.tasks.gemini_tasks import (
    _compute_retry_delay,
    _is_transient_server_error,
)


def test_detects_transient_server_errors():
    assert _is_transient_server_error("503 UNAVAILABLE") is True
    assert _is_transient_server_error("500 INTERNAL") is True
    assert _is_transient_server_error("429 RESOURCE_EXHAUSTED") is False


def test_retry_delay_grows_exponentially_with_jitter(monkeypatch):
    monkeypatch.setattr("backend.tasks.gemini_tasks.random.randint", lambda _a, _b: 2)

    assert _compute_retry_delay(0) == 7
    assert _compute_retry_delay(1) == 12
    assert _compute_retry_delay(2) == 22


def test_retry_delay_caps_base_backoff(monkeypatch):
    monkeypatch.setattr("backend.tasks.gemini_tasks.random.randint", lambda _a, _b: 1)

    assert _compute_retry_delay(10) == 61
