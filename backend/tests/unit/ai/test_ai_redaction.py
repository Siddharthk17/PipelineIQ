"""Regression tests for outbound AI prompt redaction."""

from backend.ai.redaction import sanitize_text_for_ai, sanitize_yaml_for_ai


def test_sanitize_yaml_masks_secret_values_and_url_credentials():
    sanitized = sanitize_yaml_for_ai(
        """
pipeline:
  name: secret_pipeline
  steps:
    - name: load_private
      type: load
      password: super-secret
      connection_url: postgresql://alice:secret@example.com/db
      api_key: AIzaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""".strip()
    )

    assert "super-secret" not in sanitized
    assert "alice:secret" not in sanitized
    assert "AIza" not in sanitized
    assert "password: <redacted>" in sanitized
    assert "postgresql://<redacted>@example.com/db" in sanitized


def test_sanitize_text_masks_tokens_and_secret_assignments():
    sanitized = sanitize_text_for_ai(
        "Authorization: Bearer eyJabc.defghijklmnop.qrstuvwxyz12345 "
        "token=abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz"
    )

    assert "Bearer <redacted>" in sanitized
    assert "abcdefghijklmnopqrstuvwxyz0123456789" not in sanitized
    assert "token=<redacted>" in sanitized
