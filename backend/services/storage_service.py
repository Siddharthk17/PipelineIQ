import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional, Union

import boto3
from botocore.exceptions import ClientError
from backend.config import settings


class StorageProvider(ABC):
    """Abstract base class for file storage providers."""

    @abstractmethod
    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        """Upload a file and return the stored path/key."""
        pass

    @abstractmethod
    async def upload_stream(self, stream, destination_path: str, max_size: int) -> int:
        """Upload a stream of bytes and return total bytes written."""
        pass

    @abstractmethod
    def download(self, path: str) -> BinaryIO:
        """Download a file as a binary stream."""
        pass

    @abstractmethod
    def delete(self, path: str) -> None:
        """Delete a file from storage."""
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a file exists in storage."""
        pass

    @abstractmethod
    def get_size(self, path: str) -> int:
        """Return file size in bytes."""
        pass

    @abstractmethod
    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> Optional[str]:
        """Return a presigned URL for direct upload to storage. Return None if not supported."""
        pass


class LocalStorageProvider(StorageProvider):
    """Local filesystem storage provider."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_full_path(self, path: str) -> Path:
        # Prevent path traversal by taking only the filename
        filename = os.path.basename(path)
        return self.base_dir / filename

    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        full_path = self._get_full_path(destination_path)
        with open(full_path, "wb") as f:
            shutil.copyfileobj(file_obj, f)
        return str(full_path)

    async def upload_stream(self, stream, destination_path: str, max_size: int) -> int:
        full_path = self._get_full_path(destination_path)
        total_read = 0
        with open(full_path, "wb") as output:
            async for chunk in stream:
                if not chunk:
                    continue
                total_read += len(chunk)
                if total_read > max_size:
                    output.close()
                    full_path.unlink(missing_ok=True)
                    raise Exception(f"File size exceeds maximum ({max_size} bytes)")
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
    ) -> Optional[str]:
        return None


class S3StorageProvider(StorageProvider):
    """Amazon S3 (or MinIO) storage provider."""

    def __init__(self):
        self.s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        self.bucket = settings.S3_BUCKET

    def upload(self, file_obj: BinaryIO, destination_path: str) -> str:
        key = os.path.basename(destination_path)
        self.s3.upload_fileobj(file_obj, self.bucket, key)
        return key

    async def upload_stream(self, stream, destination_path: str, max_size: int) -> int:
        import io

        buffer = io.BytesIO()
        total_read = 0
        async for chunk in stream:
            if not chunk:
                continue
            total_read += len(chunk)
            if total_read > max_size:
                raise Exception(f"File size exceeds maximum ({max_size} bytes)")
            buffer.write(chunk)

        if total_read == 0:
            raise Exception("Uploaded file is empty")

        buffer.seek(0)
        key = os.path.basename(destination_path)
        self.s3.upload_fileobj(buffer, self.bucket, key)
        return total_read

    def download(self, path: str) -> BinaryIO:
        key = os.path.basename(path)
        import io

        buffer = io.BytesIO()
        self.s3.download_fileobj(self.bucket, key, buffer)
        buffer.seek(0)
        return buffer

    def delete(self, path: str) -> None:
        key = os.path.basename(path)
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, path: str) -> bool:
        key = os.path.basename(path)
        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def get_size(self, path: str) -> int:
        key = os.path.basename(path)
        response = self.s3.head_object(Bucket=self.bucket, Key=key)
        return response["ContentLength"]

    def get_presigned_upload_url(
        self, destination_path: str, expiration: int = 3600
    ) -> Optional[str]:
        key = os.path.basename(destination_path)
        try:
            return self.s3.generate_presigned_url(
                ClientMethod="put_object",
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

    async def upload_stream(self, stream, destination_path: str, max_size: int) -> int:
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
    ) -> Optional[str]:
        return self.provider.get_presigned_upload_url(destination_path, expiration)


# Singleton instance
storage_service = StorageService()
