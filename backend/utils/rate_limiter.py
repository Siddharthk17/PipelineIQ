"""Rate limiting configuration for PipelineIQ API.

Uses slowapi with Redis backend for distributed rate limiting.

When Redis is unreachable the limiter fails OPEN to an in-memory backend
(M-034): rate limiting degrades to per-process accounting instead of
turning every rate-limited endpoint into a 500/503. The fallback still
enforces the configured limits — it just cannot share counters across
replicas until Redis recovers (slowapi probes and heals automatically).

Auth decisions are unaffected: JWT revocation stays fail-closed in
``backend/auth.py``. This fallback applies to rate limiting only.
"""

import logging
import socket
from urllib.parse import urlparse

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.config import settings

logger = logging.getLogger(__name__)


def client_ip_key(request: Request) -> str:
    """Rate-limit key: the client IP as seen by the edge proxy.

    The bundled nginx overwrites X-Real-IP with the peer address, so it
    cannot be spoofed through the standard deployment. Deployments that
    expose the API without that proxy must overwrite X-Real-IP at their
    own edge, otherwise all clients share one rate-limit bucket (and a
    spoofed header could evade limits). Falls back to the direct peer IP
    when the header is absent (tests, dev without proxy).
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    return get_remote_address(request)


def _redis_reachable(url: str, timeout: float = 0.5) -> bool:
    """Best-effort TCP probe of the Redis host (never raises)."""
    try:
        parts = urlparse(url)
        host = parts.hostname or "localhost"
        port = parts.port or 6379
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def log_rate_limit_backend_status() -> str:
    """Startup self-check: log which rate-limit backend is active.

    Returns ``"redis"`` when the configured Redis is reachable, else
    ``"memory"`` (slowapi in-memory fallback). Never raises.
    """
    try:
        if _redis_reachable(settings.REDIS_CACHE_URL):
            logger.info(
                "Rate limiter backend: redis (%s)", settings.REDIS_CACHE_URL)
            return "redis"
    except Exception:
        logger.warning(
            "Rate limiter backend check failed", exc_info=True)
    logger.warning(
        "Rate limiter backend: redis unreachable — using in-memory "
        "fallback (per-process limits until Redis recovers)")
    return "memory"


limiter = Limiter(
    key_func=client_ip_key,
    headers_enabled=True,
    storage_uri=settings.REDIS_CACHE_URL,
    # M-034: fail open to per-process memory accounting when Redis is
    # down, instead of erroring every rate-limited request. Auth
    # revocation stays fail-closed (backend/auth.py) — this fallback
    # applies to rate limiting only.
    in_memory_fallback_enabled=True,
)

# Startup self-check (best-effort, never raises): records which backend
# is active so a Redis outage is visible in logs from boot.
_rate_limit_backend = log_rate_limit_backend_status()
