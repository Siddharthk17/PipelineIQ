"""Network egress validation helpers."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    """Raised when a user-supplied URL points at a disallowed target."""


def validate_public_http_url(url: str) -> str:
    """Return a normalized HTTP(S) URL only if it resolves to public IPs."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeURL("URL must start with http:// or https://")
    if not parsed.hostname:
        raise UnsafeURL("URL must include a hostname")
    if parsed.username or parsed.password:
        raise UnsafeURL("URL credentials are not allowed")

    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("URL port is invalid") from exc
    if port is not None and not (1 <= port <= 65535):
        raise UnsafeURL("URL port is invalid")

    host = parsed.hostname
    try:
        infos = socket.getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UnsafeURL("URL hostname could not be resolved") from exc

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURL("URL hostname could not be resolved")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise UnsafeURL("URL resolves to a private or reserved address")

    return url
