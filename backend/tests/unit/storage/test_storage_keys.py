"""Regression tests for hierarchical storage keys (S02).

Storage keys must keep their hierarchy: flattening them with basename()
lets two runs overwrite each other's objects and makes readers fetch
another run's data. Path traversal outside the key space must be refused.
"""

import io

import pytest

from backend.services.storage_service import (
    LocalStorageProvider,
    S3StorageProvider,
    _validate_storage_key,
)


class _FakeS3:
    """Records keys instead of talking to S3."""

    def __init__(self):
        self.objects: dict = {}
        self.calls: list = []

    def upload_fileobj(self, file_obj, bucket, key):
        self.calls.append(("upload_fileobj", key))
        self.objects[key] = file_obj.read()

    def download_fileobj(self, bucket, key, buffer):
        self.calls.append(("download_fileobj", key))
        buffer.write(self.objects[key])

    def delete_object(self, Bucket, Key):
        self.calls.append(("delete_object", Key))
        self.objects.pop(Key, None)

    def head_object(self, Bucket, Key):
        self.calls.append(("head_object", Key))
        if Key not in self.objects:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}


def _local_provider(tmp_path):
    return LocalStorageProvider(tmp_path / "uploads")


def test_local_hierarchical_keys_do_not_collide(tmp_path):
    provider = _local_provider(tmp_path)
    provider.upload(io.BytesIO(b"run1-data"), "outputs/run1/out.csv")
    provider.upload(io.BytesIO(b"run2-data"), "outputs/run2/out.csv")

    assert provider.exists("outputs/run1/out.csv")
    assert provider.download("outputs/run1/out.csv").read() == b"run1-data"
    assert provider.download("outputs/run2/out.csv").read() == b"run2-data"
    assert provider.get_size("outputs/run1/out.csv") == len(b"run1-data")


def test_local_delete_only_removes_exact_key(tmp_path):
    provider = _local_provider(tmp_path)
    provider.upload(io.BytesIO(b"run1-data"), "outputs/run1/out.csv")
    provider.upload(io.BytesIO(b"run2-data"), "outputs/run2/out.csv")
    provider.delete("outputs/run1/out.csv")
    assert not provider.exists("outputs/run1/out.csv")
    assert provider.download("outputs/run2/out.csv").read() == b"run2-data"


@pytest.mark.parametrize(
    "key", ["../evil.csv", "/abs.csv", "a/../../evil.csv", "", "a/../.."])
def test_local_traversal_keys_rejected(tmp_path, key):
    provider = _local_provider(tmp_path)
    with pytest.raises(ValueError):
        provider.upload(io.BytesIO(b"x"), key)


def test_s3_keeps_full_key(tmp_path):
    provider = S3StorageProvider.__new__(S3StorageProvider)
    fake = _FakeS3()
    provider.s3 = fake
    provider.bucket = "test-bucket"

    provider.upload(io.BytesIO(b"run1-data"), "outputs/run1/out.csv")
    provider.upload(io.BytesIO(b"run2-data"), "outputs/run2/out.csv")

    assert fake.objects["outputs/run1/out.csv"] == b"run1-data"
    assert fake.objects["outputs/run2/out.csv"] == b"run2-data"
    assert provider.get_size("outputs/run1/out.csv") == len(b"run1-data")
    provider.delete("outputs/run1/out.csv")
    assert "outputs/run1/out.csv" not in fake.objects
    assert "outputs/run2/out.csv" in fake.objects


def test_s3_traversal_keys_rejected_before_any_call():
    provider = S3StorageProvider.__new__(S3StorageProvider)
    fake = _FakeS3()
    provider.s3 = fake
    provider.bucket = "test-bucket"
    with pytest.raises(ValueError):
        provider.download("../evil.csv")
    assert fake.calls == []


@pytest.mark.parametrize(
    "key, expected",
    [
        ("outputs/run1/out.csv", "outputs/run1/out.csv"),
        ("./uploads/abc.csv", "uploads/abc.csv"),
        ("a/../b.csv", "b.csv"),
    ],
)
def test_validate_storage_key_normalizes(key, expected):
    assert _validate_storage_key(key) == expected


@pytest.mark.parametrize(
    "key", ["", "/abs.csv", "../x.csv", "a/../../x.csv", "x\x00.csv"])
def test_validate_storage_key_rejects(key):
    with pytest.raises(ValueError):
        _validate_storage_key(key)
