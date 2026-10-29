"""Unit tests for error-message sanitization in pipeline_tasks (S-new).

The sanitizer must preserve actionable detail for pipeline owners
(e.g. a misspelled SQL column) while redacting filesystem paths and
secret-like values.
"""

from backend.tasks.pipeline_tasks import _sanitize_error_message


def test_none_stays_none():
    assert _sanitize_error_message(None) is None


def test_actionable_message_preserved():
    err = 'Binder Error: Referenced column "j2_intentional_boom" not found in FROM clause!'
    assert _sanitize_error_message(err) == err


def test_filesystem_paths_redacted():
    err = "IO Error: Could not read file /var/lib/pipelineiq/uploads/abc.csv: not found"
    out = _sanitize_error_message(err)
    assert "/var/lib/pipelineiq/uploads/abc.csv" not in out
    assert "[path]" in out
    assert "Could not read file" in out


def test_secrets_redacted():
    err = "Upload failed with api_key=supersecret123 for user"
    out = _sanitize_error_message(err)
    assert "supersecret123" not in out
    assert "[redacted]" in out


def test_empty_falls_back_to_generic():
    out = _sanitize_error_message("   ")
    assert out is not None and "run ID" in out


def test_long_messages_truncated():
    out = _sanitize_error_message("x" * 5000)
    assert out is not None and len(out) <= 2000
