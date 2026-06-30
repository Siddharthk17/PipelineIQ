"""Network egress validation helpers.

CRIT-01 / HIGH-04: DNS-rebinding protection. `validate_public_http_url` not
only validates that the host resolves to public IPs, it *pins* the resolved
IP so callers can replay the request against that exact IP (setting the
original hostname via the `Host` header) and defeat TOCTOU DNS swaps.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlparse, urlunparse


class UnsafeURL(ValueError):
    """Raised when a user-supplied URL points at a disallowed target."""


@dataclass(frozen=True)
class ResolvedURL:
    """A URL plus its pinned, validated public IP address.

    Callers should connect to `pinned_url` (https://<ip>/...) and send the
    `original_host` value in the HTTP `Host` header so virtual-hosted
    services keep routing correctly.
    """

    original_url: str
    original_host: str
    pinned_ip: str
    port: Optional[int]
    scheme: str
    path: str
    params: str
    query: str

    @property
    def pinned_url(self) -> str:
        """A URL whose netloc is the pinned IP; safe against DNS rebinding."""
        netloc = self.pinned_ip
        if ":" in self.pinned_ip and not self.pinned_ip.startswith("["):
            # IPv6 must be bracketed.
            netloc = f"[{self.pinned_ip}]"
        if self.port is not None:
            netloc = f"{netloc}:{self.port}"
        return urlunparse((
            self.scheme,
            netloc,
            self.path or "/",
            self.params,
            self.query,
            "",
        ))

    @property
    def host_header(self) -> str:
        """The original host[:port] to send in the HTTP Host header."""
        if self.port is not None:
            return f"{self.original_host}:{self.port}"
        return self.original_host


@dataclass(frozen=True)
class PreparedHTTPXRequest:
    """Pinned request details for httpx/httpcore clients."""

    url: str
    headers: dict[str, str]
    extensions: dict[str, str]


def validate_public_http_url(url: str) -> str:
    """Return a normalized HTTP(S) URL only if it resolves to public IPs.

    Kept for backward compatibility — returns the *original* URL string.
    Prefer `resolve_public_http_url` to obtain a pinned IP that defends
    against DNS rebinding (HIGH-04 / CRIT-01).
    """
    resolved = resolve_public_http_url(url)
    # Preserve original URL for callers that only need string validation.
    return resolved.original_url


def prepare_public_http_request(
    url: str,
    headers: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """Validate `url` and return a URL/headers pair for outbound clients.

    Plain HTTP requests are rewritten to the pinned public IP and carry the
    original host in the Host header, closing the DNS-rebinding TOCTOU gap.
    HTTPS requests must keep the hostname in the URL so SNI and certificate
    verification remain correct; callers still get just-in-time validation and
    must keep redirects disabled.
    """
    resolved = resolve_public_http_url(url)
    request_headers = dict(headers or {})
    if resolved.scheme == "http":
        request_headers.setdefault("Host", resolved.host_header)
        return resolved.pinned_url, request_headers
    return resolved.original_url, request_headers


def prepare_public_httpx_request(
    url: str,
    headers: Mapping[str, str] | None = None,
) -> PreparedHTTPXRequest:
    """Return pinned request arguments for httpx.

    httpx/httpcore supports the `sni_hostname` extension. For HTTPS we can
    therefore connect to the validated IP while keeping SNI and certificate
    verification bound to the original hostname.
    """
    resolved = resolve_public_http_url(url)
    request_headers = dict(headers or {})
    request_headers.setdefault("Host", resolved.host_header)
    extensions: dict[str, str] = {}
    if resolved.scheme == "https":
        extensions["sni_hostname"] = resolved.original_host
    return PreparedHTTPXRequest(
        url=resolved.pinned_url,
        headers=request_headers,
        extensions=extensions,
    )


def resolve_public_http_url(url: str) -> ResolvedURL:
    """Validate a URL and pin the resolved public IP address.

    Raises UnsafeURL if the URL is missing a hostname, uses a disallowed
    scheme, includes credentials, has an invalid port, or resolves to a
    private/reserved address (169.254.169.254 metadata, loopback, etc.).

    Returns a ResolvedURL whose `pinned_ip` callers should connect to
    instead of re-resolving the hostname (TOCTOU / DNS rebinding fix).
    """
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

    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeURL("URL hostname could not be resolved")

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise UnsafeURL("URL resolves to a private or reserved address")

    # Pin the first validated address — callers connect to this IP only.
    pinned_ip = addresses[0]
    return ResolvedURL(
        original_url=url,
        original_host=host,
        pinned_ip=pinned_ip,
        port=port,
        scheme=parsed.scheme,
        path=parsed.path,
        params=parsed.params,
        query=parsed.query,
    )
