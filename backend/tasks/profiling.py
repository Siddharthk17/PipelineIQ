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
    ".csv": lambda handle: pd.read_csv(handle),
    ".json": lambda handle: pd.read_json(handle),
}


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
        _update_profile_status(db, file_id, "running")

        df = _load_file_from_disk(file_id)

        was_sampled = False
        if len(df) > settings.PROFILE_MAX_ROWS:
            df = df.sample(n=settings.PROFILE_SAMPLE_ROWS, random_state=42)
            was_sampled = True
            logger.info(
                f"Sampled to {settings.PROFILE_SAMPLE_ROWS} rows for file_id={file_id}"
            )

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

    except Exception as exc:
        logger.error(f"Profile failed for file_id={file_id}: {exc}", exc_info=True)
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
            raise ValueError(f"File record not found for file_id={file_id}")

        stored_path = uploaded_file.stored_path
        if not storage_service.exists(stored_path):
            raise ValueError(f"File not found at path: {stored_path}")

        extension = Path(stored_path).suffix.lower()
        loader = SUPPORTED_FORMATS.get(extension)
        if loader is None:
            raise ValueError(f"Unsupported file format: {extension}")

        with closing(storage_service.download(stored_path)) as handle:
            df = loader(handle)

        df = df.convert_dtypes(
            convert_string=False,
            convert_integer=True,
            convert_floating=True,
            convert_boolean=True,
        )

        return df

    finally:
        db.close()


def _update_profile_status(
    db, file_id: str, status: str, error: str | None = None
) -> None:
    """Update the profile status in the database."""
    from sqlalchemy import update

    file_uuid = as_uuid(file_id)
    existing = db.query(FileProfile).filter(FileProfile.file_id == file_uuid).first()
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
    existing = db.query(FileProfile).filter(FileProfile.file_id == file_uuid).first()
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
