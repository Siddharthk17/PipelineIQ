"""File upload and listing API endpoints.

Handles CSV and JSON file uploads with validation for file size,
extension, and content parsing. Uploaded files are stored on disk
with metadata persisted to the database.
"""

# Standard library
import logging
import os
import uuid
from pathlib import Path
from typing import List

# Third-party packages
import pandas as pd
from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

# Internal modules
from backend.config import settings
from backend.dependencies import get_db_dependency
from backend.models import UploadedFile
from backend.schemas import FileListResponse, FileUploadResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/upload",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a data file",
    description="Upload a CSV or JSON file for use in pipeline steps.",
)
async def upload_file(
    file: UploadFile,
    db: Session = get_db_dependency(),
) -> FileUploadResponse:
    """Upload a data file and store its metadata."""
    original_filename = os.path.basename(file.filename or "upload.csv")
    _validate_file_extension(original_filename)

    # Stream-read with size enforcement to prevent OOM
    content = await _read_with_size_limit(file)

    file_id = str(uuid.uuid4())
    stored_path = _store_file(file_id, original_filename, content)
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

    logger.info(
        "File uploaded: id=%s, name=%s, rows=%d, columns=%d",
        file_id, original_filename, len(df), len(columns),
    )

    return FileUploadResponse(
        id=file_id,
        original_filename=original_filename,
        row_count=len(df),
        column_count=len(columns),
        columns=columns,
        dtypes=dtypes,
        file_size_bytes=len(content),
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
            id=f.id,
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
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )
    return FileUploadResponse(
        id=uploaded_file.id,
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
    db: Session = get_db_dependency(),
) -> dict:
    """Delete an uploaded file from disk and database."""
    _validate_uuid_format(file_id)
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    # Delete from disk
    stored_path = Path(uploaded_file.stored_path)
    if stored_path.exists():
        stored_path.unlink()

    # Delete from database
    db.delete(uploaded_file)
    db.commit()

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
    uploaded_file = db.query(UploadedFile).filter(UploadedFile.id == file_id).first()
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
    df = _parse_file_content(uploaded_file.original_filename, stored_path)
    preview_df = df.head(min(rows, 100))
    return {
        "file_id": file_id,
        "filename": uploaded_file.original_filename,
        "total_rows": len(df),
        "preview_rows": len(preview_df),
        "columns": list(df.columns),
        "data": preview_df.to_dict(orient="records"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _validate_uuid_format(value: str) -> None:
    """Raise 422 if the value is not a valid UUID."""
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid UUID format: '{value}'",
        )


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
    max_size = settings.MAX_UPLOAD_SIZE_BYTES
    chunks: list[bytes] = []
    total_read = 0
    chunk_size = 1024 * 1024  # 1MB chunks

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
