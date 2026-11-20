"""PII redaction and prompt-injection hardening for AI prompts.

User-controlled text (pipeline descriptions, error messages, YAML) is sent
to Gemini. Before it leaves the process it must be scrubbed of PII and
secrets. UUID file references are structural, not PII, and are preserved
so the model still sees real file_ids and column names.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+\d[\d\-.\s]{7,}\d"
    r"|\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}"
    r"|\b\d{3}[-.\s]\d{4}\b)"
)
_LABELED_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|bearer|token|secret|password|passwd|pwd)\b"
    r"\s*[:=]\s*['\"]?([^\s'\",;]+)['\"]?"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")


def _redact_labeled_secret(match: re.Match) -> str:
    return f"{match.group(1)}={REDACTED}"


def redact_pii(text: str) -> str:
    """Scrub PII/secrets from text bound for an AI prompt.

    Redacts emails, phone numbers, labeled secrets (api_key=...,
    password: ...), bearer tokens, and private-key blocks. UUIDs and
    other structural identifiers are preserved.
    """
    if not text:
        return text
    scrubbed = _EMAIL_RE.sub(REDACTED, text)
    scrubbed = _PHONE_RE.sub(REDACTED, scrubbed)
    scrubbed = _BEARER_TOKEN_RE.sub(f"bearer {REDACTED}", scrubbed)
    scrubbed = _PRIVATE_KEY_RE.sub(REDACTED, scrubbed)
    scrubbed = _LABELED_SECRET_RE.sub(_redact_labeled_secret, scrubbed)
    return scrubbed


UNTRUSTED_OPEN = "<untrusted_user_content>"
UNTRUSTED_CLOSE = "</untrusted_user_content>"


def wrap_untrusted(text: str) -> str:
    """Delimit untrusted user content inside a prompt.

    The system prompts instruct the model to treat delimited content as
    data, never as instructions.
    """
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"
