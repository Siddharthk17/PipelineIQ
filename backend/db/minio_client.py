"""MinIO client factory used by infrastructure checks and storage plumbing."""

from minio import Minio

from backend.config import settings


def get_minio_client() -> Minio:
    """Build a MinIO client from the configured S3-compatible settings."""
    endpoint = settings.S3_ENDPOINT_URL.removeprefix("http://").removeprefix(
        "https://")
    return Minio(
        endpoint,
        access_key=settings.S3_ACCESS_KEY,
        secret_key=settings.S3_SECRET_KEY,
        secure=settings.S3_ENDPOINT_URL.startswith("https://"),
    )
