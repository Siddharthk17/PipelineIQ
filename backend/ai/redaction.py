"""Redaction helpers for outbound AI prompts.

The AI layer should never send raw credentials, tokens, DSNs, or unbounded
stack traces to third-party model APIs. These helpers preserve enough
pipeline/schema shape for generation and healing while masking secrets.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from backend.config import settings

REDACTED = "<redacted>"

SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "secret_key",
    "private_key",
    "credential",
    "credentials",
    "auth",
    "dsn",
)

_KEY_VALUE_PATTERN = re.compile(
    r"(?ix)"
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"secret[_-]?key|private[_-]?key|credential|credentials|dsn)\b"
    r"(\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
)
_GOOGLE_API_KEY_PATTERN = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b")
_AWS_ACCESS_KEY_PATTERN = re.compile(r"\bA(KIA|SIA)[A-Z0-9]{16}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")
_LONG_SECRET_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_/+=-])([A-Za-z0-9_/+=-]{48,})(?![A-Za-z0-9_/+=-])"
)
_URL_PATTERN = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.IGNORECASE)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"


def _redact_url_credentials(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return raw_url
    if not parsed.username and not parsed.password:
        return raw_url

    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = f"{REDACTED}@{hostname}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def sanitize_text_for_ai(text: Any, *, max_chars: int | None = None) -> str:
    """Mask secret-like content in a string destined for an AI prompt."""
    value = "" if text is None else str(text)
    value = _URL_PATTERN.sub(_redact_url_credentials, value)
    value = _KEY_VALUE_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", value)
    value = _BEARER_PATTERN.sub(f"Bearer {REDACTED}", value)
    value = _JWT_PATTERN.sub(REDACTED, value)
    value = _GOOGLE_API_KEY_PATTERN.sub(REDACTED, value)
    value = _AWS_ACCESS_KEY_PATTERN.sub(REDACTED, value)
    value = _LONG_SECRET_PATTERN.sub(REDACTED, value)
    return _truncate(value, max_chars if max_chars is not None else settings.AI_PROMPT_MAX_TEXT_CHARS)


def sanitize_error_for_ai(error: Any) -> str:
    """Return a bounded, redacted error summary for prompts and audit rows."""
    return sanitize_text_for_ai(error, max_chars=settings.AI_PROMPT_MAX_ERROR_CHARS)


def sanitize_mapping_for_ai(value: Any) -> Any:
    """Recursively sanitize structured values while preserving shape."""
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                sanitized[key] = REDACTED
            else:
                sanitized[key] = sanitize_mapping_for_ai(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_mapping_for_ai(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_mapping_for_ai(item) for item in value]
    if isinstance(value, str):
        return sanitize_text_for_ai(value)
    return value


def sanitize_yaml_for_ai(yaml_text: str) -> str:
    """Sanitize a YAML document while preserving valid YAML when possible."""
    try:
        parsed = yaml.safe_load(yaml_text)
    except Exception:
        return sanitize_text_for_ai(yaml_text, max_chars=settings.AI_PROMPT_MAX_CONFIG_CHARS)

    sanitized = sanitize_mapping_for_ai(parsed)
    try:
        rendered = yaml.safe_dump(sanitized, sort_keys=False, allow_unicode=False)
    except Exception:
        rendered = sanitize_text_for_ai(yaml_text)
    return _truncate(rendered.strip(), settings.AI_PROMPT_MAX_CONFIG_CHARS)


def sanitize_schema_for_ai(schema: dict) -> dict:
    """Keep only schema fields useful for repairs and sanitize names/values."""
    sanitized: dict[str, dict] = {}
    for column, details in (schema or {}).items():
        safe_column = sanitize_text_for_ai(column, max_chars=160)
        if not isinstance(details, dict):
            sanitized[safe_column] = {}
            continue
        safe_details: dict[str, Any] = {}
        for key in ("dtype", "semantic_type", "null_pct"):
            if key in details:
                safe_details[key] = sanitize_mapping_for_ai(details[key])
        sanitized[safe_column] = safe_details
    return sanitized


def clamp_prompt(prompt: str) -> str:
    """Apply a final outbound prompt size cap."""
    return _truncate(prompt, settings.AI_PROMPT_MAX_CHARS)
