import os
import posixpath
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from backend.config import settings

# MinIO rejects SigV2 query-auth (AWSAccessKeyId/Signature/Expires).
# botocore may default to SigV2 for HTTP/IP endpoints, so pin SigV4.
_SIGV4 = Config(signature_version="s3v4")


def _validate_storage_key(key: str) -> str:
    """Normalize a storage key and refuse anything escaping the key space.

    Keys are relative POSIX paths (for example ``outputs/<run_id>/out.csv``).
    Absolute paths, ``..`` segments and null bytes are rejected so a key can
    never address an object outside its own hierarchy.
    """
    if not isinstance(key, str) or not key or "\x00" in key:
        raise ValueError(f"Invalid storage key: {key!r}")
    normalized = posixpath.normpath(key.replace("\\", "/"))
    if normalized in (".", "") or normalized.startswith("/"):
        raise ValueError(f"Invalid storage key: {key!r}")
    if any(part in ("", ".", "..") for part in normalized.split("/")):
        raise ValueError(f"Invalid storage key: {key!r}")
    return normalized


class StorageProvider(ABC):
    """Abstract base class for file storage providers."""




    @abstractmethod
    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        """Upload a file and return the stored path/key."""

    @abstractmethod
    async def upload_stream(
            self,
            stream,
            destination_path: str,
            max_size: int) -> int:
        """Upload a stream of bytes and return total bytes written."""

    @abstractmethod
    def download(self, path: str) -> BinaryIO:
        """Download a file as a binary stream."""

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file from storage."""

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists in storage."""

    @abstractmethod
    def get_size(self, path: str) -> int:
        """Return file size in bytes."""

    @abstractmethod
    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> str | None:
        """Return a presigned URL for direct upload to storage. Return None if not supported."""

    @abstractmethod
    def get_presigned_download_url(
        self, source_path: str, expiration: int = 3600
    ) -> str | None:
        """Return a presigned URL for direct download from storage. Return None if not supported."""


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage provider."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, path: str) -> Path:
        key = _validate_storage_key(path)
        full_path = self.base_dir / key
        resolved_base = self.base_dir.resolve()
        if full_path.resolve() != resolved_base and (
                resolved_base not in full_path.resolve().parents):
            raise ValueError(f"Storage key escapes base directory: {path!r}")
        return full_path

    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        full_path = self._get_full_path(destination_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "wb") as f:
            shutil.copyfileobj(file_obj, f)
        return str(full_path)

    async def upload_stream(
            self,
            stream,
            destination_path: str,
            max_size: int) -> int:
        full_path = self._get_full_path(destination_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        total_read = 0
        with open(full_path, "wb") as output:
            async for chunk in stream:
                if not chunk:
                    continue
                total_read += len(chunk)
                if total_read > max_size:
                    output.close()
                    full_path.unlink(missing_ok=True)
                    raise Exception(
                        f"File size exceeds maximum ({max_size} bytes)")
                output.write(chunk)
        if total_read == 0:
            full_path.unlink(missing_ok=True)
            raise Exception("Uploaded file is empty")
        return total_read

    def download(self, path: str) -> BinaryIO:
        full_path = self._get_full_path(path)
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return open(full_path, "rb")

    def delete(self, path: str) -> None:
        full_path = self._get_full_path(path)
        if full_path.exists():
            full_path.unlink()

    def exists(self, path: str) -> bool:
        return self._get_full_path(path).exists()

    def get_size(self, path: str) -> int:
        return self._get_full_path(path).stat().st_size

    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> str | None:
        return None

    def get_presigned_download_url(
        self, source_path: str, expiration: int = 3600
    ) -> str | None:
        return None


class S3StorageProvider(StorageProvider):
    """Amazon S3 (or MinIO) storage provider."""

    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
            config=_SIGV4,
        )
        self.bucket = settings.S3_BUCKET
        self._presign_client = None

    def _get_presign_client(self):
        """Boto client pointed at the browser-reachable endpoint.

        Presigned URLs embed the endpoint host in the signature, so they
        must be signed for the URL the browser will actually fetch — not
        the internal docker hostname used for server-side operations.
        """
        if self._presign_client is None:
            endpoint = (
                settings.S3_PUBLIC_ENDPOINT_URL or settings.S3_ENDPOINT_URL
            )
            if endpoint == settings.S3_ENDPOINT_URL:
                self._presign_client = self.s3
            else:
                self._presign_client = boto3.client(
                    "s3",
                    aws_access_key_id=settings.S3_ACCESS_KEY,
                    aws_secret_access_key=settings.S3_SECRET_KEY,
                    endpoint_url=endpoint,
                    config=_SIGV4,
                )
        return self._presign_client

    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        key = _validate_storage_key(destination_path)
        self.s3.upload_fileobj(file_obj, self.bucket, key)
        return key

    async def upload_stream(
            self,
            stream,
            destination_path: str,
            max_size: int) -> int:
        key = _validate_storage_key(destination_path)
        total_read = 0
        temp_file_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                temp_file_path = temp_file.name
                async for chunk in stream:
                    if not chunk:
                        continue
                    total_read += len(chunk)
                    if total_read > max_size:
                        raise Exception(
                            f"File size exceeds maximum ({max_size} bytes)")
                    temp_file.write(chunk)

            if total_read == 0:
                raise Exception("Uploaded file is empty")

            self.s3.upload_file(temp_file_path, self.bucket, key)
            return total_read
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)

    def download(self, path: str) -> BinaryIO:
        key = _validate_storage_key(path)
        import io

        buffer = io.BytesIO()
        self.s3.download_fileobj(self.bucket, key, buffer)
        buffer.seek(0)
        return buffer

    def delete(self, path: str) -> None:
        key = _validate_storage_key(path)
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, path: str) -> bool:
        key = _validate_storage_key(path)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return False

        else:
            return True
    def get_size(self, path: str) -> int:
        key = _validate_storage_key(path)
        response = self.s3.head_object(Bucket=self.bucket, Key=key)
        return response["ContentLength"]

    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> str | None:
        key = _validate_storage_key(destination_path)
        try:
            return self._get_presign_client().generate_presigned_url(
                ClientMethod="put_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiration,
            )
        except ClientError:
            return None

    def get_presigned_download_url(
        self, source_path: str, expiration: int = 3600
    ) -> str | None:
        key = _validate_storage_key(source_path)
        try:
            return self._get_presign_client().generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expiration,
            )
        except ClientError:
            return None


class StorageService:
    """Service to manage file storage, abstracting the underlying provider."""

    def __init__(self):
        if settings.STORAGE_TYPE == "s3":
            self.provider = S3StorageProvider()
        else:
            self.provider = LocalStorageProvider(Path(settings.UPLOAD_DIR))

    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        return self.provider.upload(file_obj, destination_path)

    async def upload_stream(
            self,
            stream,
            destination_path: str,
            max_size: int) -> int:
        return await self.provider.upload_stream(stream, destination_path, max_size)

    def download(self, path: str) -> BinaryIO:
        return self.provider.download(path)

    def delete(self, path: str) -> None:
        self.provider.delete(path)

    def exists(self, path: str) -> bool:
        return self.provider.exists(path)

    def get_size(self, path: str) -> int:
        return self.provider.get_size(path)

    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> str | None:
        return self.provider.get_presigned_upload_url(
            destination_path, expiration)

    def get_presigned_download_url(
        self, source_path: str, expiration: int = 3600
    ) -> str | None:
        return self.provider.get_presigned_download_url(
            source_path, expiration)


# Singleton instance
storage_service = StorageService()
