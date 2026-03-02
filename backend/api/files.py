"""File upload and listing API endpoints.

Handles CSV and JSON file uploads with validation for file size,
extension, and content parsing. Uploaded files are stored on disk
with metadata persisted to the database.
"""

# Standard library
import logging
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
    """Upload a data file and store its metadata.

    Validates file extension, reads the content, parses it into a
    DataFrame to extract column metadata, then stores the file on
    disk and records metadata in the database.

    Args:
        file: The uploaded file (multipart form data).
        db: Database session (injected).

    Returns:
        FileUploadResponse with file ID, row count, and column info.

    Raises:
        HTTPException 400: If the file extension is not supported.
        HTTPException 413: If the file exceeds the size limit.
        HTTPException 422: If the file content cannot be parsed.
    """
    _validate_file_extension(file.filename or "")
    content = await file.read()
    _validate_file_size(len(content))

    file_id = str(uuid.uuid4())
    stored_path = _store_file(file_id, file.filename or "upload.csv", content)
    df = _parse_file_content(file.filename or "", stored_path)

    columns = list(df.columns)
    dtypes = {col: str(dtype) for col, dtype in df.dtypes.items()}

    uploaded_file = UploadedFile(
        id=file_id,
        original_filename=file.filename or "unknown",
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
        file_id, file.filename, len(df), len(columns),
    )

    return FileUploadResponse(
        id=file_id,
        original_filename=file.filename or "unknown",
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
    """List all uploaded files with their metadata.

    Args:
        db: Database session (injected).

    Returns:
        FileListResponse with list of files and total count.
    """
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


# ═══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


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


def _validate_file_size(size_bytes: int) -> None:
    """Raise 413 if the file exceeds MAX_UPLOAD_SIZE_BYTES."""
    if size_bytes > settings.MAX_UPLOAD_SIZE_BYTES:
        max_mb = settings.MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size ({size_bytes} bytes) exceeds maximum ({max_mb:.0f} MB)",
        )


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
