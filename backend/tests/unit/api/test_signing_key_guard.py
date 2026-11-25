"""Regression tests: serving must fail fast on an ephemeral signing key.

Covers the CI incident where the API booted with an auto-generated
SECRET_KEY: /health and /auth/login returned 200 (each process verifies
its own tokens) while the edge middleware and sibling workers rejected
every token, so all browser specs bounced at /login with no useful error.
"""

import backend.config as config_module
from backend.auth import require_explicit_signing_key


def test_explicit_key_passes_startup_guard(monkeypatch):
    monkeypatch.setattr(config_module, "SECRET_KEY_WAS_GENERATED", False)
    require_explicit_signing_key()


def test_generated_key_fails_startup_guard_loudly(monkeypatch):
    monkeypatch.setattr(config_module, "SECRET_KEY_WAS_GENERATED", True)
    try:
        require_explicit_signing_key()
    except RuntimeError as exc:
        assert "SECRET_KEY" in str(exc)
        assert "auto-generated" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError for ephemeral key")


def test_empty_or_placeholder_key_sets_generated_flag():
    # The validator behind the flag: missing/weak keys fall back to an
    # ephemeral value instead of serving with a guessable secret.
    # (Restores the flag: other tests share this module singleton.)
    previous = config_module.SECRET_KEY_WAS_GENERATED
    try:
        settings = config_module.Settings(SECRET_KEY="")
        assert settings.SECRET_KEY not in ("", None)
        assert len(settings.SECRET_KEY) >= 32
        assert config_module.SECRET_KEY_WAS_GENERATED is True
    finally:
        config_module.SECRET_KEY_WAS_GENERATED = previous
