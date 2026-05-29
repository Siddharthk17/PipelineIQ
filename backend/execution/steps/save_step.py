"""Production save step: writes DataFrame output to MinIO.

Supported formats: CSV, JSON, Parquet
Output location: pipelineiq-outputs/outputs/{run_id}/{filename}
Download URL: presigned GET URL valid for 48 hours.
"""

import io
import logging
import math
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import orjson
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"csv", "json", "parquet"}
DOWNLOAD_URL_EXPIRY = timedelta(hours=48)
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

    content, content_type = _serialize(table, ext)

    object_name = f"outputs/{run_id}/{filename}"
    minio = _get_minio_client()

    minio.put_object(
        bucket_name="pipelineiq-outputs",
        object_name=object_name,
        data=io.BytesIO(content),
        length=len(content),
        content_type=content_type,
        metadata={
            "run-id": run_id,
            "row-count": str(table.num_rows),
            "format": ext,
        },
    )

    logger.info(
        "Saved pipeline output: run_id=%s, filename=%s, size=%.1fKB, rows=%d",
        run_id, filename, len(content) / 1024, table.num_rows)

    download_url = minio.presigned_get_object(
        bucket_name="pipelineiq-outputs",
        object_name=object_name,
        expires=DOWNLOAD_URL_EXPIRY,
    )

    return SaveResult(
        filename=filename,
        download_url=download_url,
        size_bytes=len(content),
        row_count=table.num_rows,
        format=ext,
        object_name=object_name,
        content_type=content_type,
    )


def refresh_download_url(object_name: str) -> str:
    """Generate a fresh presigned URL for an existing output file."""
    minio = _get_minio_client()
    return minio.presigned_get_object(
        bucket_name="pipelineiq-outputs",
        object_name=object_name,
        expires=DOWNLOAD_URL_EXPIRY,
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


def _serialize_csv(table: pa.Table) -> tuple[bytes, str]:
    df = table.to_pandas()
    csv_str = df.to_csv(index=False)
    return csv_str.encode("utf-8"), "text/csv"


def _serialize_json(table: pa.Table) -> tuple[bytes, str]:
    df = table.to_pandas()
    records = df.to_dict(orient="records")

    def sanitize(obj):
        if isinstance(obj, float):
            if math.isnan(obj) or math.isinf(obj):
                return None
        return obj

    sanitized = [
        {k: sanitize(v) for k, v in record.items()}
        for record in records
    ]

    content = orjson.dumps(sanitized, option=orjson.OPT_NON_STR_KEYS)
    return content, "application/json"


def _serialize_parquet(table: pa.Table) -> tuple[bytes, str]:
    buf = pa.BufferOutputStream()
    pq.write_table(
        table,
        buf,
        compression="snappy",
        use_dictionary=True,
        write_statistics=True,
    )
    return buf.getvalue().to_pybytes(), "application/octet-stream"


def _get_minio_client():
    from backend.services.storage_service import storage_service
    if hasattr(storage_service, "provider") and hasattr(
            storage_service.provider, "s3"):
        return storage_service.provider.s3

    import boto3
    from backend.config import settings
    return boto3.client(
        "s3",
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        endpoint_url=settings.S3_ENDPOINT_URL,
    )
