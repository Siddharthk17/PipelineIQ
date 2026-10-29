"""Presigned URLs must be signed for the browser-reachable host (S48).

Server-side S3 calls use the internal endpoint (e.g. http://minio:9000),
which browsers cannot resolve. Presigned URLs embed the endpoint host in
the signature, so they must be generated against S3_PUBLIC_ENDPOINT_URL
when it is set.
"""

from backend import config as config_module


def _provider(monkeypatch, public=None):
    monkeypatch.setattr(config_module.settings, "S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setattr(config_module.settings, "S3_PUBLIC_ENDPOINT_URL", public)
    monkeypatch.setattr(config_module.settings, "S3_ACCESS_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_SECRET_KEY", "test")
    monkeypatch.setattr(config_module.settings, "S3_BUCKET", "test-bucket")
    from backend.services.storage_service import S3StorageProvider

    return S3StorageProvider()


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
