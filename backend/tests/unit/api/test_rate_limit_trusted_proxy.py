"""Regression tests for M-047: X-Real-IP must only be trusted from proxy peers.

``client_ip_key`` keys every 5/min login/register gate. Before the fix it
trusted ``X-Real-IP`` verbatim, so a client reaching the API directly
(e.g. render.yaml exposes the API without the nginx edge that overwrites
the header) could rotate the header and get a fresh bucket per request.

New secure behavior:
- header honored ONLY when the direct peer IP is a trusted proxy
  (loopback or RFC1918/private range by default, plus TRUSTED_PROXY_CIDRS).
- otherwise the key is the direct peer IP, so spoofing buys nothing.
"""

from fastapi import Request

from backend.utils.rate_limiter import client_ip_key


def _request(peer: str, real_ip: str | None = None) -> Request:
    headers = []
    if real_ip is not None:
        headers.append((b"x-real-ip", real_ip.encode()))
    return Request({"type": "http", "client": (peer, 1234), "headers": headers})


class TestUntrustedPeerIgnoresSpoofedHeader:
    def test_public_peer_spoof_is_ignored(self):
        # Attacker-controlled header from a public peer: must key on peer.
        assert _request("203.0.113.5", "10.9.9.1") is not None
        assert client_ip_key(_request("203.0.113.5", "10.9.9.1")) == "203.0.113.5"

    def test_rotating_header_from_untrusted_peer_shares_one_bucket(self):
        # Rotating the header must NOT buy a fresh bucket.
        assert (
            client_ip_key(_request("203.0.113.5", "10.9.9.1"))
            == client_ip_key(_request("203.0.113.5", "10.9.9.2"))
            == "203.0.113.5"
        )

    def test_non_ip_peer_is_untrusted(self):
        # Starlette TestClient presents peer "testclient": fail closed.
        assert client_ip_key(_request("testclient", "10.9.9.1")) == "testclient"

    def test_untrusted_peer_without_header_keys_on_peer(self):
        assert client_ip_key(_request("203.0.113.5")) == "203.0.113.5"


class TestTrustedProxyPeerHonorsHeader:
    def test_loopback_peer_honors_header(self):
        assert client_ip_key(_request("127.0.0.1", "10.9.9.1")) == "10.9.9.1"

    def test_ipv6_loopback_peer_honors_header(self):
        assert client_ip_key(_request("::1", "10.9.9.1")) == "10.9.9.1"

    def test_rfc1918_peers_honor_header(self):
        # Docker/nginx peers arrive from the bridge network (typically 172.x).
        for peer in ("10.0.0.5", "172.18.0.2", "192.168.1.10"):
            assert client_ip_key(_request(peer, "10.9.9.1")) == "10.9.9.1", peer

    def test_trusted_peer_without_header_keys_on_peer(self):
        assert client_ip_key(_request("127.0.0.1")) == "127.0.0.1"

    def test_blank_header_falls_back_to_peer(self):
        assert client_ip_key(_request("127.0.0.1", "   ")) == "127.0.0.1"

    def test_invalid_header_value_falls_back_to_peer(self):
        assert client_ip_key(_request("127.0.0.1", "not-an-ip")) == "127.0.0.1"
