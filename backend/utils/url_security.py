"""Network egress validation helpers.

SSRF guard with validate-then-pin semantics.

Validating a hostname and then connecting to that hostname in a separate
step is vulnerable to DNS-rebinding TOCTOU: the address the validator saw
is not necessarily the address the HTTP client connects to. Callers must
 therefore:

1. :func:`validate_public_http_url` — resolve the hostname and reject
   non-public targets (private, loopback, link-local, multicast,
   reserved, unspecified, CGNAT ``100.64.0.0/10``, cloud metadata
   endpoints), and
2. :func:`pin_url_to_validated_ip` — resolve again, validate the resolved
   addresses, and rewrite the request to connect to one of the validated
   addresses (IP-pinned URL) while preserving the original ``Host``
   header (and TLS SNI via the ``sni_hostname`` extension), and
3. re-validate (and re-pin) every redirect hop — never let the HTTP
   client follow redirects on its own.

The pinned address is always a member of the set that was just
validated, so a rebinding race cannot steer the connection to an
address the validator never approved.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import socket
from urllib.parse import urlparse, urlunparse


class UnsafeURL(ValueError):
    """Raised when a user-supplied URL points at a disallowed target."""


# Explicitly blocked ranges. ``ipaddress`` predicates vary across Python
# versions (notably CGNAT ``is_private`` is False on 3.11 while
# ``is_global`` is False), so the ranges that matter for SSRF are listed
# explicitly instead of relying on any single predicate.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / shared address space
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, incl. cloud metadata
    ipaddress.ip_network("192.0.2.0/24"),  # documentation (TEST-NET-1)
    ipaddress.ip_network("198.51.100.0/24"),  # documentation (TEST-NET-2)
    ipaddress.ip_network("203.0.113.0/24"),  # documentation (TEST-NET-3)
    ipaddress.ip_network("::ffff:100.64.0.0/106"),  # CGNAT, IPv4-mapped IPv6
    ipaddress.ip_network("::ffff:169.254.0.0/112"),  # link-local, mapped
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)

# Literal cloud-metadata endpoints: blocked by address checks as well,
# listed here so the rejection reason names the actual hazard.
_METADATA_IPV4 = "169.254.169.254"


def _reason_for_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Return a rejection reason, or None when the address is public."""
    candidate: ipaddress.IPv4Address | ipaddress.IPv6Address = ip
    if (
        isinstance(candidate, ipaddress.IPv6Address)
        and candidate.ipv4_mapped is not None
    ):
        candidate = candidate.ipv4_mapped
    for network in _BLOCKED_NETWORKS:
        if candidate.version == network.version and candidate in network:
            if str(candidate) == _METADATA_IPV4 or (
                network.prefixlen >= 16
                and str(network).startswith("169.254.")
            ):
                return "URL resolves to a cloud metadata endpoint"
            return f"URL resolves to blocked range {network}"
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    ):
        return "URL resolves to a private or reserved address"
    return None


def resolve_public_ips(host: str, port: int) -> list[str]:
    """Resolve ``host`` and return every address after validating all.

    Raises :class:`UnsafeURL` when the name does not resolve or when ANY
    resolved address is non-public. Validating the full set (not just the
    first address) matters: resolvers may rotate answers, and the client
    could otherwise be steered to an unvalidated sibling address.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeURL("URL hostname could not be resolved") from exc

    addresses: list[str] = []
    for info in infos:
        address = str(info[4][0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise UnsafeURL("URL hostname could not be resolved")

    for address in addresses:
        bare = address.split("%")[0]  # strip IPv6 zone id before parsing
        try:
            ip = ipaddress.ip_address(bare)
        except ValueError as exc:
            raise UnsafeURL("URL hostname resolved to an invalid address") from exc
        reason = _reason_for_ip(ip)
        if reason is not None:
            raise UnsafeURL(reason)
    return addresses


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
    default_port = 443 if parsed.scheme == "https" else 80
    resolve_public_ips(host, port or default_port)

    return url


def pin_url_to_validated_ip(url: str) -> tuple[str, str, dict]:
    """Pin ``url`` to one of its currently-validated addresses.

    Returns ``(pinned_url, host_header, extensions)`` where ``pinned_url``
    replaces the hostname with a validated IP literal, ``host_header`` is
    the value the caller must send as the ``Host`` header (original host,
    plus port when non-default), and ``extensions`` carries
    ``sni_hostname`` for HTTPS so TLS still verifies the original name.

    The pinned address is drawn from a fresh resolution whose full result
    set was just validated, collapsing the DNS-rebinding TOCTOU window:
    the connection target is an address the validator approved on this
    lookup, not whatever a later lookup happens to return.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise UnsafeURL("URL must be an absolute http(s) URL")
    host = parsed.hostname
    default_port = 443 if parsed.scheme == "https" else 80
    try:
        explicit_port = parsed.port
    except ValueError as exc:
        raise UnsafeURL("URL port is invalid") from exc
    port = explicit_port or default_port

    pinned = resolve_public_ips(host, port)[0]

    host_header = host if explicit_port in (None, default_port) else f"{host}:{port}"
    url_host = f"[{pinned}]" if ":" in pinned else pinned
    netloc = url_host if explicit_port is None else f"{url_host}:{explicit_port}"
    pinned_url = urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path or "",
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
    extensions = {"sni_hostname": host} if parsed.scheme == "https" else {}
    return pinned_url, host_header, extensions


def build_webhook_signature(secret: str | None, body: bytes) -> str:
    """Build the ``sha256=`` HMAC hex digest for a webhook payload.

    Single construction shared by the sync test endpoint and the async
    delivery task so both sides sign identical bytes identically.
    """
    return hmac.new(
        (secret or "").encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
