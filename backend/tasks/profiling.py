"""Celery task for automatic data profiling.

Fires on every successful file upload.
Runs on the 'bulk' queue — never blocks the API.
"""

import logging
from collections.abc import Iterator
from contextlib import closing, suppress
from pathlib import Path
from typing import BinaryIO

import orjson
import pandas as pd

from backend.celery_app import celery_app
from backend.config import settings
from backend.database import SessionLocal
from backend.models import FileProfile, UploadedFile
from backend.profiling.analyzer import profile_dataframe
from backend.services.storage_service import storage_service
from backend.utils.uuid_utils import as_uuid

logger = logging.getLogger(__name__)

SUPPORTED_FORMATS = {
    ".csv",
    ".json",
    ".parquet",
}


class ProfileSourceNotFoundError(ValueError):
    """Raised when the uploaded file record or stored object is no longer available."""


_READ_CHUNK_SIZE = 256 * 1024
_JSON_WS = frozenset((0x20, 0x09, 0x0A, 0x0D))
_UTF8_BOM = b"\xef\xbb\xbf"


class _JsonMalformedError(ValueError):
    """Raised when streamed JSON is structurally invalid or truncated."""


def _iter_json_raw_values(handle: BinaryIO) -> Iterator[bytes]:
    """Yield raw bytes of each top-level JSON record, streaming (M-017).

    Mirrors ``backend.api.files._iter_json_raw_values`` (duplicated to avoid
    a circular import between the API and task modules). Array mode yields
    each element of a single top-level array; otherwise each top-level value
    is yielded in turn.
    """
    data = bytearray()
    scanned = 0
    eof = False
    depth = 0
    stack: list[bool] = []
    in_string = False
    escape = False
    started = False
    composite = False
    value_start = 0
    array_mode: bool | None = None
    closed_top = False
    first_read = True
    while True:
        if scanned == len(data) and not eof:
            chunk = handle.read(_READ_CHUNK_SIZE)
            if chunk:
                if first_read:
                    first_read = False
                    if chunk.startswith(_UTF8_BOM):
                        chunk = chunk[len(_UTF8_BOM):]
                data += chunk
            else:
                eof = True
        n = len(data)
        i = scanned
        while i < n:
            ch = data[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == 0x5C:
                    escape = True
                elif ch == 0x22:
                    in_string = False
            elif ch == 0x22:
                if closed_top:
                    raise _JsonMalformedError(
                        "trailing content after top-level array"
                    )
                in_string = True
                if not started:
                    if array_mode is None and depth == 0:
                        array_mode = False
                    started = True
                    value_start = i
            elif ch in _JSON_WS or ch == 0x2C:
                if started and depth == (1 if array_mode else 0):
                    yield bytes(data[value_start:i])
                    started = False
            elif ch == 0x5B or ch == 0x7B:
                if closed_top:
                    raise _JsonMalformedError(
                        "trailing content after top-level array"
                    )
                if array_mode is None and depth == 0:
                    array_mode = ch == 0x5B
                    if array_mode:
                        depth += 1
                        stack.append(True)
                        i += 1
                        continue
                if not started:
                    started = True
                    composite = True
                    value_start = i
                depth += 1
                stack.append(ch == 0x5B)
            elif ch == 0x5D or ch == 0x7D:
                want_open = ch == 0x5D
                if not stack or stack[-1] != want_open:
                    raise _JsonMalformedError("unbalanced brackets")
                yield_depth = 1 if array_mode else 0
                if started and depth == yield_depth + 1 and composite:
                    yield bytes(data[value_start:i + 1])
                    started = False
                elif started and depth == yield_depth and not composite:
                    yield bytes(data[value_start:i])
                    started = False
                stack.pop()
                depth -= 1
                if array_mode and depth == 0:
                    closed_top = True
            else:
                if closed_top:
                    raise _JsonMalformedError(
                        "trailing content after top-level array"
                    )
                if not started:
                    if array_mode is None and depth == 0:
                        array_mode = False
                    started = True
                    composite = False
                    value_start = i
            i += 1
        scanned = n
        if eof:
            break
        if started:
            del data[:value_start]
            scanned -= value_start
            value_start = 0
        else:
            data.clear()
            scanned = 0
    if in_string or escape:
        raise _JsonMalformedError("input ends inside a string")
    if array_mode and not closed_top:
        raise _JsonMalformedError("input ends inside a top-level array")
    if started:
        if depth == (1 if array_mode else 0):
            yield bytes(data[value_start:])
        else:
            raise _JsonMalformedError("input ends inside a JSON value")


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
        # M-142: derive completeness from the profile's per-column
        # null_counts instead of a second full isna pass over the frame.
        # Exactly equal to compute_completeness(df) (covered by
        # test_profile_single_pass.py).
        completeness = _completeness_from_profile(profile, len(df))

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
        with suppress(Exception):
            _update_profile_status(db, file_id, "failed", error=str(exc)[:500])
        raise self.retry(exc=exc) from exc

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


def _completeness_from_profile(profile: dict, row_count: int) -> float:
    """Overall completeness derived from a single profile pass (M-142).

    profile_dataframe() already counts nulls per column; reusing those
    counts avoids a second full-frame isna pass. Exactly equivalent to
    compute_completeness(df): sum of per-column null_count equals
    df.isna().sum().sum(), and an empty frame/columns yields 100.0.
    """
    if row_count == 0 or not profile:
        return 100.0
    total_cells = row_count * len(profile)
    null_cells = sum(col.get("null_count", 0) for col in profile.values())
    return round(100.0 - (null_cells / total_cells * 100), 2)


def _read_profile_dataframe(handle, extension: str, max_rows: int | None) -> pd.DataFrame:
    if extension == ".csv":
        return pd.read_csv(handle, nrows=max_rows)
    if extension == ".json":
        if max_rows is None:
            return pd.read_json(handle)
        # Stream records so sampling never parses the whole file (M-017).
        records = []
        for raw in _iter_json_raw_values(handle):
            try:
                value = orjson.loads(raw)
            except ValueError as exc:
                raise ValueError(f"invalid JSON value: {exc}") from exc
            records.append(value)
            if len(records) >= max_rows:
                break
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)
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
