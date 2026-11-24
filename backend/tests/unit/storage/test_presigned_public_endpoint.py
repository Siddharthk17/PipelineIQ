"""Presigned URLs must be signed for the browser-reachable host (S48).

Server-side S3 calls use the internal endpoint (e.g. http://minio:9000),
which browsers cannot resolve. Presigned URLs embed the endpoint host in
the signature, so they must be generated against S3_PUBLIC_ENDPOINT_URL
when it is set.

M-115: presigning is fail-closed — a head_bucket reachability check runs
before offline SigV4 signing. These tests stub head_bucket at the boto3
boundary (no network) while keeping the real SigV4 signer, so the
URL-structure assertions below stay genuine.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from backend import config as config_module


def _provider(monkeypatch, public=None):
    monkeypatch.setattr(config_module.settings, "S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setattr(config_module.settings, "S3_PUBLIC_ENDPOINT_URL", public)
    monkeypatch.setattr(config_module.settings, "S3_ACCESS_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_SECRET_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_BUCKET", "test-bucket")
    from backend.services.storage_service import S3StorageProvider

    provider = S3StorageProvider()
    # M-115: stub the fail-closed reachability check at the boto3 boundary
    # (no network). Signing itself still uses the real SigV4 signer.
    monkeypatch.setattr(provider.s3, "head_bucket", MagicMock(return_value={}))
    return provider


def test_presigned_url_uses_public_endpoint(monkeypatch):
    provider = _provider(monkeypatch, public="http://127.0.0.1:9000")
    url = provider.get_presigned_upload_url("uploads/abc.csv", expiration=60)
    assert url is not None
    assert url.startswith("http://127.0.0.1:9000/"), url
    assert "minio:9000" not in url


def test_presigned_url_falls_back_to_endpoint(monkeypatch):
    provider = _provider(monkeypatch, public=None)
    url = provider.get_presigned_upload_url("uploads/abc.csv", expiration=60)
    assert url is not None
    assert url.startswith("http://minio:9000/"), url


def test_public_host_preserved_in_download_url(monkeypatch):
    provider = _provider(monkeypatch, public="http://127.0.0.1:9000")
    url = provider.get_presigned_download_url("outputs/run-1/a.csv", expiration=60)
    assert url is not None
    assert url.startswith("http://127.0.0.1:9000/"), url


def test_presigned_urls_use_sigv4(monkeypatch):
    """Presigned URLs must use SigV4 (S52).

    botocore may default to SigV2 query-auth (AWSAccessKeyId/Signature/
    Expires) for HTTP/IP endpoints; MinIO rejects those for PUTs, which
    broke every direct browser upload over the large-file threshold.
    """
    provider = _provider(monkeypatch, public="http://127.0.0.1:9000")
    for url in (
        provider.get_presigned_upload_url("uploads/abc.csv", expiration=60),
        provider.get_presigned_download_url("outputs/run-1/a.csv", expiration=60),
    ):
        assert url is not None
        assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url, url
        assert "AWSAccessKeyId" not in url, url


def test_presigned_upload_raises_when_bucket_missing(monkeypatch):
    """M-115: fail closed — missing bucket raises instead of a dead URL."""
    provider = _provider(monkeypatch, public="http://127.0.0.1:9000")
    monkeypatch.setattr(
        provider.s3,
        "head_bucket",
        MagicMock(
            side_effect=ClientError(
                {"Error": {"Code": "NoSuchBucket", "Message": "bucket missing"}},
                "HeadBucket",
            )
        ),
    )
    with pytest.raises(ClientError):
        provider.get_presigned_upload_url("uploads/abc.csv", expiration=60)


def test_presigned_download_raises_when_endpoint_unreachable(monkeypatch):
    """M-115: fail closed — unreachable endpoint raises instead of a dead URL."""
    provider = _provider(monkeypatch, public=None)
    monkeypatch.setattr(
        provider.s3,
        "head_bucket",
        MagicMock(
            side_effect=EndpointConnectionError(endpoint_url="http://minio:9000")
        ),
    )
    with pytest.raises(EndpointConnectionError):
        provider.get_presigned_download_url("outputs/run-1/a.csv", expiration=60)