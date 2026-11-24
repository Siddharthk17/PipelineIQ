"""Regression tests for M-115: single credential source, cred quoting, fail-closed presign.

(a) ``backend/storage/lifecycle.py::_minio_client`` must derive endpoint and
    credentials from settings (``S3_*``). ``MINIO_*`` env vars are only a
    documented fallback when no ``S3_ENDPOINT_URL`` is configured — they must
    never silently override ``S3_*``.
(b) ``backend/execution/duckdb_executor.py`` interpolates credentials into
    ``SET s3_...='...'`` f-strings. Single quotes in a value must be escaped
    (doubled) so the value stays a pure SQL string literal.
(c) Presigning (``S3StorageProvider`` + ``save_step``) is offline SigV4
    signing with no I/O. A ``head_bucket`` reachability check must run
    before signing and any failure must raise instead of returning a URL
    that can never work.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend import config as config_module

# ---------------------------------------------------------------------------
# (a) single credential source for lifecycle MinIO client
# ---------------------------------------------------------------------------

class _CapturingMinio:
    """Stand-in for minio.Minio that records constructor args (no network)."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def _minio_client_with(settings_overrides, env_overrides, monkeypatch):
    from backend.storage import lifecycle as lc

    for name, value in settings_overrides.items():
        monkeypatch.setattr(config_module.settings, name, value)
    for name, value in env_overrides.items():
        monkeypatch.setenv(name, value)
    captured = {}

    def _factory(*args, **kwargs):
        client = _CapturingMinio(*args, **kwargs)
        captured["client"] = client
        return client

    monkeypatch.setattr(lc, "Minio", _factory)
    return lc._minio_client(), captured


def test_minio_client_prefers_s3_settings_over_minio_env(monkeypatch):
    """S3_* settings win even when MINIO_* points elsewhere (M-115a)."""
    from backend.storage import lifecycle as lc  # noqa: F401 (import check)

    _, captured = _minio_client_with(
        {
            "S3_ENDPOINT_URL": "http://s3-settings:9000",
            "S3_ACCESS_KEY": "settings-key",
            "S3_SECRET_KEY": "settings-secret",
        },
        {
            "MINIO_ENDPOINT": "minio-wrong:9000",
            "MINIO_ROOT_USER": "wrong-user",
            "MINIO_ROOT_PASSWORD": "wrong-pass",
        },
        monkeypatch,
    )
    client = captured["client"]
    assert client.args[0] == "s3-settings:9000"
    assert client.kwargs["access_key"] == "settings-key"
    assert client.kwargs["secret_key"] == "settings-secret"
    assert client.kwargs["secure"] is False


def test_minio_client_derives_secure_from_https_endpoint(monkeypatch):
    """https S3 endpoints must produce a TLS MinIO client (M-115a)."""
    _, captured = _minio_client_with(
        {
            "S3_ENDPOINT_URL": "https://s3.example.com",
            "S3_ACCESS_KEY": "settings-key",
            "S3_SECRET_KEY": "settings-secret",
        },
        {},
        monkeypatch,
    )
    client = captured["client"]
    assert client.args[0] == "s3.example.com"
    assert client.kwargs["secure"] is True


def test_minio_client_falls_back_to_minio_env_without_s3_settings(monkeypatch):
    """Documented fallback: no S3_ENDPOINT_URL -> MINIO_* env (M-115a)."""
    _, captured = _minio_client_with(
        {"S3_ENDPOINT_URL": None},
        {
            "MINIO_ENDPOINT": "minio:9000",
            "MINIO_ROOT_USER": "minio",
            "MINIO_ROOT_PASSWORD": "minio123",
        },
        monkeypatch,
    )
    client = captured["client"]
    assert client.args[0] == "minio:9000"
    assert client.kwargs["access_key"] == "minio"
    assert client.kwargs["secure"] is False


# ---------------------------------------------------------------------------
# (b) DuckDB credential quoting
# ---------------------------------------------------------------------------

class _RecordingConn:
    def __init__(self):
        self.statements = []

    def execute(self, sql):
        self.statements.append(sql)
        return self


def _httpfs_statements(monkeypatch, user, password):
    from backend.execution import duckdb_executor as dq

    monkeypatch.setenv("MINIO_ENDPOINT", "minio:9000")
    monkeypatch.setenv("MINIO_ROOT_USER", user)
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", password)
    monkeypatch.setenv("MINIO_REGION", "us-east-1")
    conn = _RecordingConn()
    dq._configure_httpfs(conn)
    return conn.statements


def test_duckdb_escapes_single_quote_in_credentials(monkeypatch):
    """A quote in a key/secret must not break out of the SET literal (M-115b)."""
    statements = _httpfs_statements(monkeypatch, "o'brien", "s3cr'e\"t")
    access = next(s for s in statements if "s3_access_key_id" in s)
    secret = next(s for s in statements if "s3_secret_access_key" in s)
    assert "o''brien" in access
    assert "o'brien" not in access.replace("o''brien", "")
    assert "s3cr''e" in secret


def test_duckdb_escaped_literal_round_trips(monkeypatch):
    """The doubled-quote literal must evaluate back to the raw value."""
    import duckdb

    statements = _httpfs_statements(monkeypatch, "o'brien", "plain")
    access = next(s for s in statements if "s3_access_key_id" in s)
    literal = access.split("=", 1)[1].strip()
    conn = duckdb.connect(database=":memory:")
    try:
        value = conn.execute(f"SELECT {literal}").fetchone()[0]
    finally:
        conn.close()
    assert value == "o'brien"


def test_duckdb_plain_credentials_unchanged(monkeypatch):
    """Credentials without quotes keep their exact statement shape (M-115b)."""
    statements = _httpfs_statements(monkeypatch, "plainkey", "plainsecret")
    assert "SET s3_access_key_id='plainkey'" in statements
    assert "SET s3_secret_access_key='plainsecret'" in statements


# ---------------------------------------------------------------------------
# (c) fail-closed presigning
# ---------------------------------------------------------------------------

def _client_error():
    return ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "bucket missing"}},
        "HeadBucket",
    )


def _unreachable_error():
    return EndpointConnectionError(endpoint_url="http://minio:9000")


def _s3_provider(monkeypatch, head_bucket_effect=None):
    from backend.services.storage_service import S3StorageProvider

    monkeypatch.setattr(config_module.settings, "S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setattr(config_module.settings, "S3_PUBLIC_ENDPOINT_URL", None)
    monkeypatch.setattr(config_module.settings, "S3_ACCESS_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_SECRET_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_BUCKET", "test-bucket")
    mock_s3 = MagicMock()
    if head_bucket_effect is not None:
        mock_s3.head_bucket.side_effect = head_bucket_effect
    else:
        mock_s3.head_bucket.return_value = {}
    mock_s3.generate_presigned_url.return_value = "https://signed/url"
    with patch("backend.services.storage_service.boto3.client", return_value=mock_s3):
        provider = S3StorageProvider()
    provider.s3 = mock_s3
    return provider, mock_s3


@pytest.mark.parametrize("error", [_client_error(), _unreachable_error()],
                         ids=["missing-bucket", "unreachable-endpoint"])
def test_presigned_upload_raises_when_bucket_unreachable(monkeypatch, error):
    """No reachable bucket -> raise, never a dead URL (M-115c)."""
    provider, mock_s3 = _s3_provider(monkeypatch, head_bucket_effect=error)
    with pytest.raises((ClientError, EndpointConnectionError)):
        provider.get_presigned_upload_url("uploads/abc.csv", expiration=60)
    mock_s3.head_bucket.assert_called_with(Bucket="test-bucket")


@pytest.mark.parametrize("error", [_client_error(), _unreachable_error()],
                         ids=["missing-bucket", "unreachable-endpoint"])
def test_presigned_download_raises_when_bucket_unreachable(monkeypatch, error):
    """No reachable bucket -> raise, never a dead URL (M-115c)."""
    provider, mock_s3 = _s3_provider(monkeypatch, head_bucket_effect=error)
    with pytest.raises((ClientError, EndpointConnectionError)):
        provider.get_presigned_download_url("outputs/run-1/a.csv", expiration=60)
    mock_s3.head_bucket.assert_called_with(Bucket="test-bucket")


def test_presigned_upload_still_signs_when_bucket_reachable(monkeypatch):
    """Reachable bucket keeps the existing signing path (M-115c compat)."""
    provider, mock_s3 = _s3_provider(monkeypatch)
    url = provider.get_presigned_upload_url("uploads/abc.csv", expiration=60)
    assert url == "https://signed/url"
    mock_s3.head_bucket.assert_called_with(Bucket="test-bucket")


def _mock_save_client(head_bucket_effect=None):
    client = MagicMock()
    if head_bucket_effect is not None:
        client.head_bucket.side_effect = head_bucket_effect
    else:
        client.head_bucket.return_value = {}
    client.generate_presigned_url.return_value = "https://minio/signed"
    return client


def _sample_table():
    import pyarrow as pa

    return pa.table({"a": [1, 2], "b": ["x", "y"]})


def _mock_step():
    step = MagicMock()
    step.filename = "output.csv"
    step.type = "save"
    return step


def test_save_step_execute_raises_when_bucket_unreachable():
    """execute_save_step must not hand out a dead download URL (M-115c)."""
    from backend.execution.steps.save_step import execute_save_step

    client = _mock_save_client(head_bucket_effect=_client_error())
    with patch(
        "backend.execution.steps.save_step._get_minio_client", return_value=client
    ), patch(
        "backend.execution.steps.save_step._get_presign_client",
        side_effect=lambda server_client: server_client,
    ), pytest.raises((ClientError, EndpointConnectionError)):
        execute_save_step(_sample_table(), _mock_step(), run_id="run-001")
    client.head_bucket.assert_called()


def test_save_step_refresh_raises_when_bucket_unreachable():
    """refresh_download_url must not hand out a dead URL (M-115c)."""
    from backend.execution.steps.save_step import refresh_download_url

    client = _mock_save_client(head_bucket_effect=_unreachable_error())
    with patch(
        "backend.execution.steps.save_step._get_minio_client", return_value=client
    ), patch(
        "backend.execution.steps.save_step._get_presign_client",
        side_effect=lambda server_client: server_client,
    ), pytest.raises((ClientError, EndpointConnectionError)):
        refresh_download_url("outputs/run-001/output.csv")
    client.head_bucket.assert_called()
