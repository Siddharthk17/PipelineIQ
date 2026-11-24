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

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.config import settings

logger = logging.getLogger(__name__)

# M-047: peers whose X-Real-IP header may be honored. Loopback and
# RFC1918/private ranges cover the bundled nginx (same host or docker
# bridge) and local dev/test. Anything else must be listed explicitly
# via TRUSTED_PROXY_CIDRS.
_DEFAULT_TRUSTED_PROXY_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _extra_trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    """Parse TRUSTED_PROXY_CIDRS into networks (invalid entries skipped)."""
    raw = getattr(settings, "TRUSTED_PROXY_CIDRS", "") or ""
    networks: list[ipaddress._BaseNetwork] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            networks.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning(
                "Ignoring invalid TRUSTED_PROXY_CIDRS entry: %r", part)
    return networks


def _peer_is_trusted_proxy(peer: str) -> bool:
    """True when the direct peer IP belongs to a trusted proxy network."""
    try:
        addr = ipaddress.ip_address(peer.strip())
    except ValueError:
        # Non-IP peer (e.g. Starlette TestClient "testclient"): fail closed.
        return False
    return any(
        addr in net
        for net in (*_DEFAULT_TRUSTED_PROXY_NETWORKS,
                    *_extra_trusted_proxy_networks())
    )


def client_ip_key(request: Request) -> str:
    """Rate-limit key: the client IP as seen by a trusted edge proxy.

    X-Real-IP is honored ONLY when the direct peer IP belongs to a
    trusted proxy (loopback or RFC1918/private range by default, plus
    TRUSTED_PROXY_CIDRS). The bundled nginx overwrites X-Real-IP with
    the peer address, so legitimate deployments keep working (nginx
    arrives from localhost or the docker bridge). Direct exposures
    (e.g. render.yaml without that edge) key on the peer IP instead,
    so a spoofed header cannot buy a fresh rate-limit bucket.

    Falls back to the direct peer IP when the header is absent, blank,
    or not a valid IP address.
    """
    peer = get_remote_address(request)
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip and _peer_is_trusted_proxy(peer):
        try:
            ipaddress.ip_address(real_ip)
        except ValueError:
            logger.warning(
                "Ignoring malformed X-Real-IP header from trusted peer %s",
                peer)
            return peer
        return real_ip
    return peer


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
