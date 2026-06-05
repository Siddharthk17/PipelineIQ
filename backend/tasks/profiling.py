"""Celery task for automatic data profiling.

Fires on every successful file upload.
Runs on the 'bulk' queue — never blocks the API.
"""

import logging
from contextlib import closing
from pathlib import Path

import pandas as pd

from backend.celery_app import celery_app
from backend.config import settings
from backend.database import SessionLocal
from backend.models import FileProfile, UploadedFile
from backend.profiling.analyzer import (
    compute_completeness,
    profile_dataframe,
)
from backend.services.storage_service import storage_service
from backend.utils.uuid_utils import as_uuid

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {
    ".csv",
    ".json",
    ".parquet",
    ".xlsx",
}


class ProfileSourceNotFoundError(ValueError):
    """Raised when the uploaded file record or stored object is no longer available."""


@celery_app.task(
    name="tasks.profile_file",
    bind=True,
    queue="bulk",
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=300,
    time_limit=360,
)
def profile_file(self, file_id: str) -> dict:
    """Profile all columns of an uploaded file."""
    db = SessionLocal()
    try:
        df = _load_file_from_disk(file_id)
        _update_profile_status(db, file_id, "running")

        was_sampled = bool(df.attrs.get("_pipelineiq_sampled"))

        profile = profile_dataframe(df)
        completeness = compute_completeness(df)

        if was_sampled:
            for col_profile in profile.values():
                col_profile["sampled"] = True
                col_profile["sample_size"] = settings.PROFILE_SAMPLE_ROWS

        _save_profile(
            db=db,
            file_id=file_id,
            profile=profile,
            row_count=len(df),
            col_count=len(df.columns),
            completeness_pct=completeness,
        )

        logger.info(
            f"Profile complete for file_id={file_id}: "
            f"{len(df.columns)} columns, {completeness}% complete"
        )

        return {
            "file_id": file_id,
            "row_count": len(df),
            "col_count": len(df.columns),
            "completeness_pct": completeness,
        }

    except ProfileSourceNotFoundError as exc:
        logger.warning(
            "Skipping profile for missing source file_id=%s: %s",
            file_id,
            exc)
        return {
            "file_id": file_id,
            "status": "skipped",
            "reason": "file_not_found",
        }
    except Exception as exc:
        logger.error(
            f"Profile failed for file_id={file_id}: {exc}",
            exc_info=True)
        try:
            _update_profile_status(db, file_id, "failed", error=str(exc)[:500])
        except Exception:
            pass
        raise self.retry(exc=exc)

    finally:
        db.close()


def _load_file_from_disk(file_id: str) -> pd.DataFrame:
    """Load a file from storage and return as a DataFrame."""
    db = SessionLocal()
    try:
        file_uuid = as_uuid(file_id)
        uploaded_file = (
            db.query(UploadedFile).filter(UploadedFile.id == file_uuid).first()
        )
        if uploaded_file is None:
            raise ProfileSourceNotFoundError(
                f"File record not found for file_id={file_id}"
            )

        stored_path = uploaded_file.stored_path
        if not storage_service.exists(stored_path):
            raise ProfileSourceNotFoundError(
                f"File not found at path: {stored_path}")

        extension = Path(stored_path).suffix.lower()
        if extension not in SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported file format: {extension}")

        should_sample = bool(
            uploaded_file.row_count
            and uploaded_file.row_count > settings.PROFILE_MAX_ROWS
        )
        max_rows = settings.PROFILE_SAMPLE_ROWS if should_sample else None
        with closing(storage_service.download(stored_path)) as handle:
            df = _read_profile_dataframe(handle, extension, max_rows=max_rows)

        df = df.convert_dtypes(
            convert_string=False,
            convert_integer=True,
            convert_floating=True,
            convert_boolean=True,
        )
        if should_sample:
            df.attrs["_pipelineiq_sampled"] = True
            logger.info(
                "Loaded bounded profile sample: file_id=%s rows=%d/%d",
                file_id,
                len(df),
                uploaded_file.row_count,
            )

        return df

    finally:
        db.close()


def _read_profile_dataframe(handle, extension: str, max_rows: int | None) -> pd.DataFrame:
    if extension == ".csv":
        return pd.read_csv(handle, nrows=max_rows)
    if extension == ".json":
        if max_rows is None:
            return pd.read_json(handle)
        try:
            return pd.read_json(handle, lines=True, nrows=max_rows)
        except ValueError:
            handle.seek(0)
            return pd.read_json(handle).head(max_rows)
    if extension == ".parquet":
        if max_rows is None:
            return pd.read_parquet(handle)
        import pyarrow as pa
        import pyarrow.parquet as pq

        parquet_file = pq.ParquetFile(handle)
        tables = []
        rows = 0
        for index in range(parquet_file.num_row_groups):
            table = parquet_file.read_row_group(index)
            remaining = max_rows - rows
            if table.num_rows > remaining:
                table = table.slice(0, remaining)
            tables.append(table)
            rows += table.num_rows
            if rows >= max_rows:
                break
        return pa.concat_tables(tables).to_pandas() if tables else pd.DataFrame()
    if extension == ".xlsx":
        return pd.read_excel(handle, engine="openpyxl", nrows=max_rows)
    raise ValueError(f"Unsupported file format: {extension}")


def _update_profile_status(
    db, file_id: str, status: str, error: str | None = None
) -> None:
    """Update the profile status in the database."""
    from sqlalchemy import update

    file_uuid = as_uuid(file_id)
    file_exists = (
        db.query(
            UploadedFile.id).filter(
            UploadedFile.id == file_uuid).first() is not None)
    if not file_exists:
        logger.warning(
            "Skipping profile status update for missing uploaded_file file_id=%s",
            file_id,
        )
        return

    existing = db.query(FileProfile).filter(
        FileProfile.file_id == file_uuid).first()
    if existing:
        stmt = (
            update(FileProfile)
            .where(FileProfile.file_id == file_uuid)
            .values(status=status, error=error)
        )
        db.execute(stmt)
    else:
        profile = FileProfile(
            file_id=file_uuid,
            status=status,
            error=error,
        )
        db.add(profile)
    db.commit()


def _save_profile(
    db,
    file_id: str,
    profile: dict,
    row_count: int,
    col_count: int,
    completeness_pct: float,
) -> None:
    """Upsert the profile into the database."""
    file_uuid = as_uuid(file_id)
    existing = db.query(FileProfile).filter(
        FileProfile.file_id == file_uuid).first()
    if existing:
        existing.profile = profile
        existing.row_count = row_count
        existing.col_count = col_count
        existing.completeness_pct = completeness_pct
        existing.status = "complete"
        existing.error = None
    else:
        db.add(
            FileProfile(
                file_id=file_uuid,
                profile=profile,
                row_count=row_count,
                col_count=col_count,
                completeness_pct=completeness_pct,
                status="complete",
                error=None,
            )
        )
    db.commit()
