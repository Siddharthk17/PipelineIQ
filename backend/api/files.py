"""File upload and listing API endpoints.

Handles CSV and JSON file uploads with validation for file size,
extension, and content parsing. Uploaded files are stored on disk
with metadata persisted to the database.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.dependencies import get_db_dependency
from backend.models import SchemaSnapshot, UploadedFile, User
from backend.pipeline.schema_drift import detect_schema_drift
from backend.utils.uuid_utils import validate_uuid_format as _validate_uuid_format, as_uuid as _as_uuid

from backend.schemas import (
    ColumnDriftResponse,
    FileListResponse,
    FileUploadResponse,
    SchemaDriftResponse,
)
from backend.utils.rate_limiter import limiter
from backend.services.audit_service import log_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a data file",
    description="Upload a CSV or JSON file for use in pipeline steps.",
)
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def upload_file(
    request: Request,
    file: UploadFile,
    response: Response,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileUploadResponse:
    """Upload a data file and store its metadata."""
    original_filename = os.path.basename(file.filename or "upload.csv")
    _validate_file_extension(original_filename)

    # Stream-read with size enforcement to prevent OOM
    content = await _read_with_size_limit(file)

    file_id = uuid.uuid4()
    stored_path = _store_file(str(file_id), original_filename, content)
    df = _parse_file_content(original_filename, stored_path)

    columns = list(df.columns)
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

    uploaded_file = UploadedFile(
        id=file_id,
        original_filename=original_filename,
        stored_path=str(stored_path),
        file_size_bytes=len(content),
        row_count=len(df),
        column_count=len(columns),
        columns=columns,
        dtypes=dtypes,
    )
    db.add(uploaded_file)
    db.commit()

    # Save schema snapshot for drift detection
    snapshot = SchemaSnapshot(
        file_id=file_id,
        columns=columns,
        dtypes=dtypes,
        row_count=len(df),
    )
    db.add(snapshot)
    db.commit()

    # Check for schema drift against previous snapshot of same filename
    drift_report = None
    previous_snapshot = (
        db.query(SchemaSnapshot)
        .join(UploadedFile, SchemaSnapshot.file_id == UploadedFile.id)
        .filter(
            UploadedFile.original_filename == original_filename,
            SchemaSnapshot.file_id != _as_uuid(file_id),
        )
        .order_by(SchemaSnapshot.captured_at.desc())
        .first()
    )
    if previous_snapshot:
        drift_report = detect_schema_drift(
            old_columns=previous_snapshot.columns,
            old_dtypes=previous_snapshot.dtypes,
            new_columns=columns,
            new_dtypes=dtypes,
        )

    # Build schema drift response
    schema_drift_response = None
    if drift_report is not None:
        drift_items = []
        for col in drift_report.columns_removed:
            drift_items.append(ColumnDriftResponse(
                column=col, drift_type="removed",
                old_value=None, new_value=None, severity="breaking",
            ))
        for col in drift_report.columns_added:
            drift_items.append(ColumnDriftResponse(
                column=col, drift_type="added",
                old_value=None, new_value=None, severity="info",
            ))
        for tc in drift_report.type_changes:
            drift_items.append(ColumnDriftResponse(
                column=tc.column, drift_type="type_changed",
                old_value=tc.old_type, new_value=tc.new_type,
                severity="warning",
            ))
        breaking = sum(1 for d in drift_items if d.severity == "breaking")
        warns = sum(1 for d in drift_items if d.severity == "warning")
        schema_drift_response = SchemaDriftResponse(
            has_drift=drift_report.has_drift,
            breaking_changes=breaking,
            warnings=warns,
            drift_items=drift_items,
        )

    logger.info(
        "File uploaded: id=%s, name=%s, rows=%d, columns=%d",
        file_id, original_filename, len(df), len(columns),
    )

    from backend.metrics import FILES_UPLOADED_TOTAL
    FILES_UPLOADED_TOTAL.inc()

    log_action(db, "file_uploaded", user_id=current_user.id, resource_type="file",
               resource_id=file_id, details={"filename": original_filename, "row_count": len(df)},
               request=request)

    return FileUploadResponse(
        id=str(file_id),
        original_filename=original_filename,
        row_count=len(df),
        column_count=len(columns),
        columns=columns,
        dtypes=dtypes,
        file_size_bytes=len(content),
        schema_drift=schema_drift_response,
    )

@router.get(
    "/",
    response_model=FileListResponse,
    summary="List uploaded files",
    description="Returns metadata for all uploaded files.",
)
def list_files(db: Session = get_db_dependency()) -> FileListResponse:
    """List all uploaded files with their metadata."""
    files = db.query(UploadedFile).all()
    file_responses = [
        FileUploadResponse(
            id=str(f.id),
            original_filename=f.original_filename,
            row_count=f.row_count,
            column_count=f.column_count,
            columns=f.columns,
            dtypes=f.dtypes,
            file_size_bytes=f.file_size_bytes,
        )
        for f in files
    ]
    return FileListResponse(files=file_responses, total=len(file_responses))

@router.get(
    "/{file_id}",
    response_model=FileUploadResponse,
    summary="Get file metadata",
    description="Returns metadata for a specific uploaded file.",
)
def get_file(
    file_id: str,
    db: Session = get_db_dependency(),
) -> FileUploadResponse:
    """Get metadata for a specific uploaded file."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == _as_uuid(file_id)).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )
    return FileUploadResponse(
        id=str(uploaded_file.id),
        original_filename=uploaded_file.original_filename,
        row_count=uploaded_file.row_count,
        column_count=uploaded_file.column_count,
        columns=uploaded_file.columns,
        dtypes=uploaded_file.dtypes,
        file_size_bytes=uploaded_file.file_size_bytes,
    )

@router.delete(
    "/{file_id}",
    summary="Delete an uploaded file",
    description="Removes the file from disk and database.",
)
def delete_file(
    file_id: str,
    request: Request,
    db: Session = get_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete an uploaded file from disk and database."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == _as_uuid(file_id)).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    stored_path = Path(uploaded_file.stored_path)
    if stored_path.exists():
        stored_path.unlink()

    db.delete(uploaded_file)
    db.commit()

    log_action(db, "file_deleted", user_id=current_user.id, resource_type="file",
               resource_id=_as_uuid(file_id), request=request)

    logger.info("File deleted: id=%s", file_id)
    return {"detail": f"File '{file_id}' deleted"}

@router.get(
    "/{file_id}/preview",
    summary="Preview file data",
    description="Returns the first N rows of an uploaded file as JSON records.",
)
def preview_file(
    file_id: str,
    rows: int = 20,
    db: Session = get_db_dependency(),
) -> dict:
    """Return the first N rows of an uploaded file."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == _as_uuid(file_id)).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )
    stored_path = Path(uploaded_file.stored_path)
    if not stored_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File data not found on disk for '{file_id}'",
        )
    df = _parse_file_preview(uploaded_file.original_filename, stored_path, min(rows, 100))
    return {
        "file_id": file_id,
        "filename": uploaded_file.original_filename,
        "total_rows": uploaded_file.row_count,
        "preview_rows": len(df),
        "columns": list(df.columns),
        "data": df.to_dict(orient="records"),
    }

def _validate_file_extension(filename: str) -> None:
    """Raise 400 if the file extension is not in ALLOWED_EXTENSIONS."""
    extension = Path(filename).suffix.lower()
    if extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file extension '{extension}'. "
                f"Allowed: {sorted(settings.ALLOWED_EXTENSIONS)}"
            ),
        )

async def _read_with_size_limit(file: UploadFile) -> bytes:
    """Read file content with streaming size enforcement to prevent OOM."""
    max_size = settings.MAX_UPLOAD_SIZE
    chunks: list[bytes] = []
    total_read = 0
    chunk_size = 1024 * 1024

    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total_read += len(chunk)
        if total_read > max_size:
            max_mb = max_size / (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds maximum ({max_mb:.0f} MB)",
            )
        chunks.append(chunk)

    content = b"".join(chunks)
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )
    return content

def _store_file(file_id: str, original_filename: str, content: bytes) -> Path:
    """Write the file content to disk in the upload directory."""
    extension = Path(original_filename).suffix.lower()
    stored_path = settings.UPLOAD_DIR / f"{file_id}{extension}"
    stored_path.write_bytes(content)
    return stored_path

def _parse_file_content(filename: str, stored_path: Path) -> pd.DataFrame:
    """Parse the stored file into a DataFrame for metadata extraction."""
    extension = stored_path.suffix.lower()
    try:
        if extension == ".csv":
            return pd.read_csv(stored_path)
        elif extension == ".json":
            return pd.read_json(stored_path)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot parse file with extension '{extension}'",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc


def _parse_file_preview(filename: str, stored_path: Path, nrows: int) -> pd.DataFrame:
    """Parse only the first N rows of a file for preview (avoids reading full file)."""
    extension = stored_path.suffix.lower()
    try:
        if extension == ".csv":
            return pd.read_csv(stored_path, nrows=nrows)
        elif extension == ".json":
            df = pd.read_json(stored_path)
            return df.head(nrows)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot parse file with extension '{extension}'",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc

@router.get(
    "/{file_id}/schema/history",
    summary="Get schema snapshot history",
    description="Returns all schema snapshots for a file, newest first.",
)
def get_schema_history(
    file_id: str,
    db: Session = get_db_dependency(),
) -> dict:
    """Get schema snapshot history for a file."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == _as_uuid(file_id)).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    snapshots = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.file_id == _as_uuid(file_id))
        .order_by(SchemaSnapshot.captured_at.desc())
        .all()
    )

    return {
        "file_id": file_id,
        "total_snapshots": len(snapshots),
        "snapshots": [
            {
                "id": str(s.id),
                "columns": s.columns,
                "dtypes": s.dtypes,
                "row_count": s.row_count,
                "captured_at": s.captured_at.isoformat() if s.captured_at else None,
            }
            for s in snapshots
        ],
    }

@router.get(
    "/{file_id}/schema/diff",
    summary="Get schema drift between latest snapshots",
    description="Compares the two most recent schema snapshots for drift.",
)
def get_schema_diff(
    file_id: str,
    db: Session = get_db_dependency(),
) -> dict:
    """Get schema drift between the two most recent snapshots."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == _as_uuid(file_id)).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    snapshots = (
        db.query(SchemaSnapshot)
        .filter(SchemaSnapshot.file_id == _as_uuid(file_id))
        .order_by(SchemaSnapshot.captured_at.desc())
        .limit(2)
        .all()
    )

    if len(snapshots) < 2:
        return {
            "file_id": file_id,
            "has_drift": False,
            "message": "Not enough snapshots for comparison",
        }

    current = snapshots[0]
    previous = snapshots[1]
    report = detect_schema_drift(
        old_columns=previous.columns,
        old_dtypes=previous.dtypes,
        new_columns=current.columns,
        new_dtypes=current.dtypes,
    )

    return {
        "file_id": file_id,
        "has_drift": report.has_drift,
        "columns_added": report.columns_added,
        "columns_removed": report.columns_removed,
        "type_changes": [
            {
                "column": d.column,
                "old_type": d.old_type,
                "new_type": d.new_type,
            }
            for d in report.type_changes
        ],
        "summary": report.summary,
    }
