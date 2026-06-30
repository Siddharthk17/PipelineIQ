"""Integrity helpers for Redis-backed SSE progress events."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import orjson

from backend.config import settings

SSE_SIGNATURE_FIELD = "__sig"


def _sse_secret() -> bytes:
    """CRIT-02: isolated SSE signing key (HKDF-derived from SECRET_KEY).

    Falls back to SECRET_KEY only when SSE_SECRET_KEY was not configured,
    preserving backward compatibility with already-signed streams.
    """
    secret = getattr(settings, "SSE_SECRET_KEY", "") or settings.SECRET_KEY
    return secret.encode("utf-8")


def _signature_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {
        key: value for key, value in payload.items()
        if key != SSE_SIGNATURE_FIELD
    }
    return orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)


def sign_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    signed = dict(payload)
    signature = hmac.new(
        _sse_secret(),
        _signature_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    signed[SSE_SIGNATURE_FIELD] = signature
    return signed


def verify_sse_payload(payload: dict[str, Any]) -> bool:
    signature = payload.get(SSE_SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        return settings.ENVIRONMENT == "test"
    expected = hmac.new(
        _sse_secret(),
        _signature_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def public_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_payload = dict(payload)
    public_payload.pop(SSE_SIGNATURE_FIELD, None)
    return public_payload
