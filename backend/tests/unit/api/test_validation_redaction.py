"""M-055: the 422 handler must never leak secret values.

Field names are kept (debuggability) but values for secret-bearing
keys are replaced with [REDACTED], long strings truncated, and the
logged/response payload stays JSON-serializable.
"""

from backend.main import _looks_sensitive, _redact_log_value


def test_sensitive_keys_detected():
    for key in ("password", "passwd", "secret", "token", "api_key",
                "access_token", "client_secret", "authorization",
                "cookie", "credential", "private_key", "otp"):
        assert _looks_sensitive(key)
        assert _looks_sensitive(key.upper())
    for key in ("email", "username", "role", "amount"):
        assert not _looks_sensitive(key)


def test_redact_replaces_secret_values_but_keeps_names():
    redacted = _redact_log_value(None, {
        "email": "a@b.c",
        "password": "S3cret!",
        "nested": {"token": "abc", "ok": "fine"},
    })
    assert redacted["password"] == "[REDACTED]"
    assert redacted["nested"]["token"] == "[REDACTED]"
    assert redacted["email"] == "a@b.c"
    assert redacted["nested"]["ok"] == "fine"
    assert "password" in redacted and "token" in redacted["nested"]


def test_redact_truncates_and_caps():
    redacted = _redact_log_value(None, {
        "ok": "z" * 500,
        "items": list(range(50)),
    })
    assert redacted["ok"] == "z" * 200
    assert len(redacted["items"]) < 50
    assert redacted["items"][-1].startswith("...")


def test_redact_stays_json_serializable_with_ctx_objects():
    import json

    redacted = _redact_log_value(
        None, {"ctx": {"error": ValueError("Invalid email format")}})
    json.dumps(redacted)
    assert "Invalid email format" in redacted["ctx"]["error"]


def test_422_response_redacts_password_input(auth_client):
    # The 422 JSON body echoes field names but must not echo the
    # attempted password value.
    from backend.tests.unit.api.test_auth import register_user

    r = register_user(auth_client, "redact@test.com", "redactuser", "short")
    assert r.status_code == 422
    body = r.json()
    assert body["error_type"] == "ValidationError"
    pw_errors = [
        e for e in body["details"]
        if isinstance(e.get("loc"), list) and "password" in e["loc"]
    ]
    assert pw_errors, body["details"]
    for err in pw_errors:
        assert err.get("input") == "[REDACTED]"
        assert "short" not in str(err)
