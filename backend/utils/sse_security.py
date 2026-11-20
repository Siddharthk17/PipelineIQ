"""Integrity helpers for Redis-backed SSE progress events."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import orjson

from backend.config import settings

SSE_SIGNATURE_FIELD = "__sig"


def _signature_payload(payload: dict[str, Any]) -> bytes:
    unsigned = {
        key: value for key, value in payload.items()
        if key != SSE_SIGNATURE_FIELD
    }
    return orjson.dumps(unsigned, option=orjson.OPT_SORT_KEYS)


def sign_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    signed = dict(payload)
    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _signature_payload(signed),
        hashlib.sha256,
    ).hexdigest()
    signed[SSE_SIGNATURE_FIELD] = signature
    return signed


def _unsigned_allowed() -> bool:
    """Return True only when unsigned SSE payloads are explicitly opted in.

    Opt-in is via ``PIPELINEIQ_ALLOW_UNSIGNED_SSE=true`` and never applies
    in production, even if the flag is set. The previous behavior (accept
    unsigned payloads whenever ``ENVIRONMENT == "test"``) failed open:
    any test-env Redis writer could forge progress events. Default is
    deny (M-156).
    """
    if settings.ENVIRONMENT == "production":
        return False
    return os.getenv("PIPELINEIQ_ALLOW_UNSIGNED_SSE", "false").lower() == "true"


def verify_sse_payload(payload: dict[str, Any]) -> bool:
    signature = payload.get(SSE_SIGNATURE_FIELD)
    if not isinstance(signature, str) or not signature:
        return _unsigned_allowed()
    expected = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        _signature_payload(payload),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def public_sse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_payload = dict(payload)
    public_payload.pop(SSE_SIGNATURE_FIELD, None)
    return public_payload
