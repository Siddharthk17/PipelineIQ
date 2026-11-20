"""Regression tests for M-107/M-171: generic retry/backoff helpers.

The orphan-sweep (M-107) and webhook fan-out redelivery (M-171 leftovers)
share one backoff policy so retries stay bounded and jittered instead of
thundering-herd on the next beat tick.
"""

import pytest

from backend.tasks.retry_helpers import compute_backoff_delay, retry_sync


class TestComputeBackoffDelay:
    def test_first_attempt_returns_base(self):
        assert compute_backoff_delay(0, base_seconds=30.0, jitter_ratio=0.0) == 30.0

    def test_grows_exponentially(self):
        delays = [
            compute_backoff_delay(n, base_seconds=10.0, jitter_ratio=0.0)
            for n in range(4)
        ]
        assert delays == [10.0, 20.0, 40.0, 80.0]

    def test_capped_at_max(self):
        assert (
            compute_backoff_delay(100, base_seconds=30.0, jitter_ratio=0.0)
            == 3600.0
        )

    def test_negative_attempt_clamped_to_base(self):
        assert compute_backoff_delay(-3, base_seconds=5.0, jitter_ratio=0.0) == 5.0

    def test_jitter_stays_within_ratio(self):
        for _ in range(50):
            d = compute_backoff_delay(2, base_seconds=100.0, jitter_ratio=0.1)
            assert 360.0 <= d <= 440.0


class TestRetrySync:
    def test_succeeds_first_try(self):
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        assert retry_sync(fn, attempts=3) == "ok"
        assert len(calls) == 1

    def test_retries_then_succeeds_without_sleeping(self):
        calls = []
        sleeps = []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("boom")
            return "ok"

        import backend.tasks.retry_helpers as helpers

        monkeypatch_sleep = helpers._sleep
        helpers._sleep = sleeps.append
        try:
            assert (
                retry_sync(
                    fn,
                    attempts=3,
                    exceptions=(ConnectionError,),
                    base_seconds=1.0,
                )
                == "ok"
            )
        finally:
            helpers._sleep = monkeypatch_sleep
        assert len(calls) == 3
        assert len(sleeps) == 2

    def test_raises_after_exhausting_attempts(self):
        def fn():
            raise ConnectionError("down")

        import backend.tasks.retry_helpers as helpers

        monkeypatch_sleep = helpers._sleep
        helpers._sleep = lambda _: None  # noqa: E731
        try:
            with pytest.raises(ConnectionError):
                retry_sync(fn, attempts=2, base_seconds=0.01)
        finally:
            helpers._sleep = monkeypatch_sleep

    def test_unexpected_exception_propagates_immediately(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            retry_sync(fn, attempts=3, exceptions=(ConnectionError,))
        assert len(calls) == 1
