"""Rate limiting configuration for PipelineIQ API.

Uses slowapi with Redis backend for distributed rate limiting.
"""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.config import settings


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


limiter = Limiter(
    key_func=client_ip_key,
    headers_enabled=True,
    storage_uri=settings.REDIS_CACHE_URL,
)
