"""Production save step: writes Arrow output to object storage.

Supported formats: CSV, JSON, Parquet
Output location: {S3_BUCKET}/outputs/{run_id}/{filename}
Download URL: presigned GET URL valid for 1 hour.
"""

import io
import logging
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import orjson
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from backend.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"csv", "json", "parquet"}
DOWNLOAD_URL_EXPIRY = timedelta(hours=1)
DEFAULT_CONTENT_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "parquet": "application/octet-stream",
}


@dataclass
class SaveResult:
    filename: str
    download_url: str
    size_bytes: int
    row_count: int
    format: str
    object_name: str
    content_type: str = ""
    warnings: list[str] = field(default_factory=list)


def execute_save_step(
    table: pa.Table,
    step,
    run_id: str,
) -> SaveResult:
    """Write an Arrow Table to MinIO and return a presigned download URL.

    Args:
        table:   The Arrow Table containing the pipeline output data.
        step:    The SaveStep model instance (or dict) with filename config.
        run_id:  UUID of the pipeline run (used as path prefix in MinIO).

    Returns:
        SaveResult with download_url, size_bytes, row_count, format.
    """
    filename = _resolve_filename(step, run_id)
    ext = _resolve_extension(filename)
    filename = _ensure_extension(filename, ext)

    if not run_id:
        import uuid
        run_id = uuid.uuid4().hex
        logger.debug("Empty run_id, using generated: %s", run_id)

    temp_path, content_type, size_bytes = _serialize_to_tempfile(table, ext)

    object_name = f"outputs/{run_id}/{filename}"
    minio = _get_minio_client()

    bucket = settings.S3_BUCKET
    try:
        with open(temp_path, "rb") as body:
            minio.put_object(
                Bucket=bucket,
                Key=object_name,
                Body=body,
                ContentLength=size_bytes,
                ContentType=content_type,
                Metadata={
                    "run-id": run_id,
                    "row-count": str(table.num_rows),
                    "format": ext,
                },
            )
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

    logger.info(
        "Saved pipeline output: run_id=%s, filename=%s, size=%.1fKB, rows=%d",
        run_id, filename, size_bytes / 1024, table.num_rows)

    download_url = minio.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": object_name,
        },
        ExpiresIn=int(DOWNLOAD_URL_EXPIRY.total_seconds()),
    )

    return SaveResult(
        filename=filename,
        download_url=download_url,
        size_bytes=size_bytes,
        row_count=table.num_rows,
        format=ext,
        object_name=object_name,
        content_type=content_type,
    )


def refresh_download_url(object_name: str) -> str:
    """Generate a fresh presigned URL for an existing output file."""
    minio = _get_minio_client()
    return minio.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.S3_BUCKET,
            "Key": object_name,
        },
        ExpiresIn=int(DOWNLOAD_URL_EXPIRY.total_seconds()),
    )


def _resolve_filename(step, run_id: str) -> str:
    filename = getattr(step, "filename", None)
    if isinstance(step, dict):
        filename = step.get("filename")
    if not filename:
        filename = f"output_{run_id[:8]}.csv"
    return filename


def _resolve_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if not ext or ext not in SUPPORTED_EXTENSIONS:
        ext = "csv"
    return ext


def _ensure_extension(filename: str, ext: str) -> str:
    name = Path(filename).stem
    return f"{name}.{ext}"


def _serialize(table: pa.Table, ext: str) -> tuple[bytes, str]:
    if ext == "csv":
        return _serialize_csv(table)
    if ext == "json":
        return _serialize_json(table)
    if ext == "parquet":
        return _serialize_parquet(table)
    raise ValueError(f"Unsupported format: {ext}")


def _serialize_to_tempfile(table: pa.Table, ext: str) -> tuple[str, str, int]:
    suffix = f".{ext}"
    fd, temp_path = tempfile.mkstemp(prefix="pipelineiq-save-", suffix=suffix)
    os.close(fd)
    try:
        if ext == "csv":
            _write_csv_file(table, temp_path)
        elif ext == "json":
            _write_json_file(table, temp_path)
        elif ext == "parquet":
            _write_parquet_file(table, temp_path)
        else:
            raise ValueError(f"Unsupported format: {ext}")
        size_bytes = os.path.getsize(temp_path)
        return temp_path, DEFAULT_CONTENT_TYPES[ext], size_bytes
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _write_csv_file(table: pa.Table, path: str) -> None:
    pacsv.write_csv(table, path)


def _sanitize_json_value(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {key: _sanitize_json_value(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json_value(value) for value in obj]
    return obj


def _write_json_file(table: pa.Table, path: str) -> None:
    first = True
    with open(path, "wb") as output:
        output.write(b"[")
        for batch in table.to_batches(max_chunksize=8192):
            for record in batch.to_pylist():
                if not first:
                    output.write(b",")
                output.write(orjson.dumps(_sanitize_json_value(record), option=orjson.OPT_NON_STR_KEYS))
                first = False
        output.write(b"]")


def _write_parquet_file(table: pa.Table, path: str) -> None:
    pq.write_table(
        table,
        path,
        compression="snappy",
        use_dictionary=True,
        write_statistics=True,
    )


def _serialize_csv(table: pa.Table) -> tuple[bytes, str]:
    temp_path, content_type, _ = _serialize_to_tempfile(table, "csv")
    try:
        with open(temp_path, "rb") as handle:
            return handle.read(), content_type
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _serialize_json(table: pa.Table) -> tuple[bytes, str]:
    temp_path, content_type, _ = _serialize_to_tempfile(table, "json")
    try:
        with open(temp_path, "rb") as handle:
            return handle.read(), content_type
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


def _serialize_parquet(table: pa.Table) -> tuple[bytes, str]:
    temp_path, content_type, _ = _serialize_to_tempfile(table, "parquet")
    try:
        with open(temp_path, "rb") as handle:
            return handle.read(), content_type
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass


_bucket_ensured = False


def _get_minio_client():
    from backend.services.storage_service import storage_service
    if hasattr(storage_service, "provider") and hasattr(
            storage_service.provider, "s3"):
        client = storage_service.provider.s3
    else:
        import boto3
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )

    global _bucket_ensured
    if not _bucket_ensured:
        _bucket_ensured = True
        try:
            client.head_bucket(Bucket=settings.S3_BUCKET)
            logger.debug("S3 bucket exists: %s", settings.S3_BUCKET)
        except Exception:
            try:
                client.create_bucket(Bucket=settings.S3_BUCKET)
                logger.info("Created S3 bucket: %s", settings.S3_BUCKET)
            except client.exceptions.BucketAlreadyOwnedByYou:
                logger.debug("S3 bucket already owned: %s", settings.S3_BUCKET)
            except client.exceptions.BucketAlreadyExists:
                logger.warning("S3 bucket name conflict: %s", settings.S3_BUCKET)
            except Exception as exc:
                logger.warning("Failed to initialize S3 bucket: %s", exc)

    return client
