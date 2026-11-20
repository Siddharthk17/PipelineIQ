"""Generic retry/backoff helpers shared by periodic maintenance tasks.

The orphan-sweep (M-107) and webhook fan-out redelivery (M-171 leftovers)
share one backoff policy: exponential growth with symmetric jitter and a
hard cap, so retries stay bounded and do not thunder-herd on the next
beat tick.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_BASE_SECONDS = 30.0
DEFAULT_MAX_SECONDS = 3600.0
DEFAULT_JITTER_RATIO = 0.1


def _sleep(seconds: float) -> None:
    """Sleep seam (monkeypatchable in tests to avoid real waiting)."""
    time.sleep(seconds)


def compute_backoff_delay(
    attempt: int,
    *,
    base_seconds: float = DEFAULT_BASE_SECONDS,
    multiplier: float = 2.0,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
) -> float:
    """Delay before retry number ``attempt`` (0-based).

    Exponential: ``base * multiplier**attempt``, capped at ``max_seconds``,
    then symmetric jitter of +/- ``jitter_ratio``. Negative attempts clamp
    to the base delay; the result is always non-negative.
    """
    if attempt < 0:
        attempt = 0
    delay = base_seconds * (multiplier**attempt)
    delay = min(delay, max_seconds)
    if jitter_ratio > 0:
        span = delay * jitter_ratio
        delay = delay + random.uniform(-span, span)
    return max(0.0, delay)


def retry_sync(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    base_seconds: float = 1.0,
    multiplier: float = 2.0,
    max_seconds: float = 30.0,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
    action: str = "operation",
) -> T:
    """Run ``fn`` with bounded exponential-backoff retries.

    Only ``exceptions`` are retried; anything else propagates immediately.
    The final failure re-raises after ``attempts`` total tries. Sleeps go
    through :func:`compute_backoff_delay` so every caller shares the policy.
    """
    last_exc: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except exceptions as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                break
            delay = compute_backoff_delay(
                attempt,
                base_seconds=base_seconds,
                multiplier=multiplier,
                max_seconds=max_seconds,
                jitter_ratio=jitter_ratio,
            )
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                action,
                attempt + 1,
                attempts,
                exc,
                delay,
            )
            _sleep(delay)
    assert last_exc is not None
    raise last_exc
