"""File upload and listing API endpoints.

Handles CSV and JSON file uploads with validation for file size,
extension, and content parsing. Uploaded files are stored on disk
with metadata persisted to the database.
"""

import csv
import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional, BinaryIO

import orjson
import pandas as pd
from fastapi import (
    APIRouter,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.db.redis_pools import get_cache_redis
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import SchemaSnapshot, UploadedFile, User
from backend.pipeline.schema_drift import detect_schema_drift
from backend.utils.uuid_utils import (
    validate_uuid_format as _validate_uuid_format,
    as_uuid as _as_uuid,
)

from backend.schemas import (
    ColumnDriftResponse,
    FileListResponse,
    FileUploadResponse,
    SchemaDriftResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from backend.utils.rate_limiter import limiter
from backend.services.audit_service import log_action
from backend.services.storage_service import storage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
legacy_router = APIRouter(prefix="/files", tags=["files-legacy"])

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024
DIRECT_UPLOAD_TTL_SECONDS = 30 * 60
MAX_DIRECT_UPLOAD_SIZE = settings.MAX_UPLOAD_SIZE
PENDING_UPLOAD_PREFIX = "files:pending-upload:"


def _pending_upload_key(file_id: str) -> str:
    return f"{PENDING_UPLOAD_PREFIX}{file_id}"


def _cache_pending_upload(file_id: str, payload: dict) -> None:
    try:
        get_cache_redis().setex(
            _pending_upload_key(file_id),
            DIRECT_UPLOAD_TTL_SECONDS,
            orjson.dumps(payload),
        )
    except RedisError:
        logger.warning("Failed to cache pending upload state for file_id=%s", file_id)


def _get_pending_upload(file_id: str) -> Optional[dict]:
    try:
        raw = get_cache_redis().get(_pending_upload_key(file_id))
    except RedisError:
        logger.warning("Failed to read pending upload state for file_id=%s", file_id)
        return None
    if not raw:
        return None
    try:
        return orjson.loads(raw)
    except Exception:
        logger.warning("Invalid pending upload payload for file_id=%s", file_id)
        return None


def _clear_pending_upload(file_id: str) -> None:
    try:
        get_cache_redis().delete(_pending_upload_key(file_id))
    except RedisError:
        logger.warning("Failed to clear pending upload state for file_id=%s", file_id)


@router.post(
    "/request-upload-url",
    response_model=UploadUrlResponse,
    summary="Negotiate upload strategy",
    description="Returns API upload path for small files and direct upload details for large files.",
)
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def request_upload_url(
    request: Request,
    response: Response,
    body: Optional[UploadUrlRequest] = Body(default=None),
    filename: Optional[str] = Query(default=None),
    file_size: Optional[int] = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
) -> UploadUrlResponse:
    """Negotiate file upload method based on file size."""
    request_payload = _resolve_upload_request_payload(body, filename, file_size)

    filename = os.path.basename(request_payload.filename or "")
    if not filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filename is required",
        )
    _validate_file_extension(filename)

    if request_payload.file_size > MAX_DIRECT_UPLOAD_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum ({MAX_DIRECT_UPLOAD_SIZE // (1024 * 1024)} MB)",
        )

    file_id = str(uuid.uuid4())
    extension = Path(filename).suffix.lower()
    stored_path = settings.UPLOAD_DIR / f"{file_id}{extension}"

    request_path = request.url.path
    is_legacy_api = request_path.startswith("/api/files/")
    api_prefix = "/api/files" if is_legacy_api else "/api/v1/files"
    absolute_api_prefix = f"{str(request.base_url).rstrip('/')}{api_prefix}"

    if request_payload.file_size > LARGE_FILE_THRESHOLD:
        _cache_pending_upload(
            file_id,
            {
                "filename": filename,
                "expected_size": request_payload.file_size,
                "stored_path": str(stored_path),
                "user_id": str(current_user.id),
            },
        )
        return UploadUrlResponse(
            method="direct",
            file_id=file_id,
            upload_url=(
                f"{absolute_api_prefix}/direct-upload/{file_id}"
                if is_legacy_api
                else f"{api_prefix}/direct-upload/{file_id}"
            ),
            confirm_endpoint=(
                f"{absolute_api_prefix}/{file_id}/confirm"
                if is_legacy_api
                else f"{api_prefix}/{file_id}/confirm"
            ),
        )

    return UploadUrlResponse(
        method="api",
        file_id=file_id,
        upload_endpoint=(
            f"{absolute_api_prefix}/upload" if is_legacy_api else f"{api_prefix}/upload"
        ),
    )


@router.put(
    "/direct-upload/{file_id}",
    summary="Direct upload target",
    description="Large-file direct upload target. Streams request body to storage path.",
)
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def direct_upload_file(
    file_id: str,
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Receive direct upload payload and stream it to disk without buffering entire content."""
    _validate_uuid_format(file_id)
    pending = _get_pending_upload(file_id)
    if pending is None or pending.get("user_id") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not pending confirmation",
        )

    stored_path = Path(pending["stored_path"])
    expected_size = int(pending["expected_size"])
    try:
        written = await storage_service.upload_stream(
            request.stream(), str(stored_path), MAX_DIRECT_UPLOAD_SIZE
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    if written != expected_size:
        storage_service.delete(str(stored_path))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded size mismatch: expected {expected_size} bytes, got {written} bytes",
        )
    return {"file_id": file_id, "status": "uploaded", "size_bytes": written}


@router.post(
    "/{file_id}/confirm",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm direct upload",
    description="Confirms a prior direct upload and finalizes file metadata.",
)
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def confirm_direct_upload(
    file_id: str,
    request: Request,
    response: Response,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileUploadResponse:
    """Confirm a direct upload and persist its metadata."""
    _validate_uuid_format(file_id)
    pending = _get_pending_upload(file_id)
    if pending is None or pending.get("user_id") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not pending confirmation",
        )

    stored_path = pending["stored_path"]
    if not storage_service.exists(stored_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found in storage. Upload may have failed.",
        )

    finalized = _finalize_uploaded_file(
        db=db,
        file_id=_as_uuid(file_id),
        original_filename=pending["filename"],
        stored_path=str(stored_path),
        file_size_bytes=storage_service.get_size(str(stored_path)),
        request=request,
        current_user=current_user,
    )
    _clear_pending_upload(file_id)
    return finalized


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
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileUploadResponse:
    """Upload a data file and store its metadata."""
    # Handle empty or missing filename - this causes 422 errors
    if not file.filename or file.filename.strip() == "":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No file provided or filename is empty. Please select a valid CSV or JSON file.",
        )

    original_filename = os.path.basename(file.filename or "upload.csv")
    _validate_file_extension(original_filename)

    file_id = uuid.uuid4()
    extension = Path(original_filename).suffix.lower()
    stored_path = f"{file_id}{extension}"

    try:
        storage_service.upload(file.file, stored_path)
        written = storage_service.get_size(stored_path)
        if written == 0:
            storage_service.delete(stored_path)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty",
            )
        if written > settings.MAX_UPLOAD_SIZE:
            storage_service.delete(stored_path)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File size exceeds maximum ({settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB)",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {exc}",
        )

    return _finalize_uploaded_file(
        db=db,
        file_id=file_id,
        original_filename=original_filename,
        stored_path=stored_path,
        file_size_bytes=written,
        request=request,
        current_user=current_user,
    )


@router.get(
    "/",
    response_model=FileListResponse,
    summary="List uploaded files",
    description="Returns metadata for all uploaded files belonging to the current user.",
)
def list_files(
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileListResponse:
    """List all uploaded files with their metadata."""
    files = db.query(UploadedFile).filter(UploadedFile.user_id == current_user.id).all()
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
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileUploadResponse:
    """Get metadata for a specific uploaded file."""
    _validate_uuid_format(file_id)
    uploaded_file = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
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
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Delete an uploaded file from disk and database."""
    _validate_uuid_format(file_id)
    uploaded_file = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    stored_path = uploaded_file.stored_path
    storage_service.delete(stored_path)

    db.delete(uploaded_file)
    db.commit()

    log_action(
        db,
        "file_deleted",
        user_id=current_user.id,
        resource_type="file",
        resource_id=_as_uuid(file_id),
        request=request,
    )

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
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return the first N rows of an uploaded file."""
    _validate_uuid_format(file_id)
    uploaded_file = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
    if uploaded_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    stored_path = uploaded_file.stored_path
    if not storage_service.exists(stored_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File data not found in storage for '{file_id}'",
        )

    with storage_service.download(stored_path) as handle:
        df = _parse_file_preview(
            uploaded_file.original_filename, handle, min(rows, 100)
        )
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


def _resolve_upload_request_payload(
    body: Optional[UploadUrlRequest],
    filename: Optional[str],
    file_size: Optional[int],
) -> UploadUrlRequest:
    """Support both JSON body and query-parameter upload negotiation contracts."""
    if body is not None:
        return body
    if filename is None or file_size is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="filename and file_size are required",
        )
    return UploadUrlRequest(filename=filename, file_size=file_size)


def _extract_file_metadata(
    filename: str, stored_path: str
) -> tuple[int, list[str], dict[str, str]]:
    """Extract row/column metadata with streaming for CSV uploads."""
    extension = Path(filename).suffix.lower()
    if extension == ".csv":
        return _extract_csv_metadata(stored_path)
    if extension == ".json":
        return _extract_json_metadata(filename, stored_path)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Cannot parse file with extension '{extension}'",
    )


def _extract_csv_metadata(stored_path: str) -> tuple[int, list[str], dict[str, str]]:
    """Read CSV metadata with bounded memory usage."""
    try:
        with storage_service.download(stored_path) as handle:
            # Convert binary stream to text wrapper for csv reader
            import io

            text_handle = io.TextIOWrapper(handle, encoding="utf-8", newline="")
            reader = csv.reader(text_handle)
            header = next(reader, None)
            if header is None:
                return 0, [], {}
            row_count = sum(1 for _ in reader)
            columns = [str(column) for column in header]
    except UnicodeDecodeError:
        try:
            with storage_service.download(stored_path) as handle:
                import io

                text_handle = io.TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                reader = csv.reader(text_handle)
                header = next(reader, None)
                if header is None:
                    return 0, [], {}
                row_count = sum(1 for _ in reader)
                columns = [str(column) for column in header]
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to parse file '{stored_path}': {exc}",
            ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{stored_path}': {exc}",
        ) from exc

    if not columns:
        return row_count, columns, {}

    sample_rows = max(1, min(1000, row_count))
    with storage_service.download(stored_path) as handle:
        sample_df = pd.read_csv(handle, nrows=sample_rows)
    dtypes = {column: str(dtype) for column, dtype in sample_df.dtypes.items()}
    return row_count, columns, dtypes


def _extract_json_metadata(
    filename: str, stored_path: str
) -> tuple[int, list[str], dict[str, str]]:
    """Extract metadata for JSON uploads."""
    try:
        # Try JSONL (newline-delimited JSON) first for efficiency
        with storage_service.download(stored_path) as handle:
            import io

            text_handle = io.TextIOWrapper(handle, encoding="utf-8")
            first_line = text_handle.readline()
            if not first_line:
                return 0, [], {}

            if first_line.strip().startswith("{"):
                # Process as JSONL
                row_count = 1
                with storage_service.download(stored_path) as handle:
                    # Re-open to read all lines
                    import io

                    text_handle = io.TextIOWrapper(handle, encoding="utf-8")
                    for _ in text_handle:
                        row_count += 1

                import orjson

                # We already have first_line from the first attempt
                first_obj = orjson.loads(first_line)
                columns = list(first_obj.keys())

                # Sample first line for dtypes
                sample_df = pd.DataFrame([first_obj])
                dtypes = {col: str(dtype) for col, dtype in sample_df.dtypes.items()}
                return row_count - 1, columns, dtypes

        # Fallback to standard JSON list
        with storage_service.download(stored_path) as handle:
            df = pd.read_json(handle)
            columns = list(df.columns)
            dtypes = {column: str(dtype) for column, dtype in df.dtypes.items()}
            return len(df), columns, dtypes
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc


def _build_schema_drift_response(
    db: Session,
    file_id,
    original_filename: str,
    columns: list[str],
    dtypes: dict,
) -> Optional[SchemaDriftResponse]:
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
    if drift_report is None:
        return None

    drift_items = []
    for col in drift_report.columns_removed:
        drift_items.append(
            ColumnDriftResponse(
                column=col,
                drift_type="removed",
                old_value=None,
                new_value=None,
                severity="breaking",
            )
        )
    for col in drift_report.columns_added:
        drift_items.append(
            ColumnDriftResponse(
                column=col,
                drift_type="added",
                old_value=None,
                new_value=None,
                severity="info",
            )
        )
    for tc in drift_report.type_changes:
        drift_items.append(
            ColumnDriftResponse(
                column=tc.column,
                drift_type="type_changed",
                old_value=tc.old_type,
                new_value=tc.new_type,
                severity="warning",
            )
        )

    breaking = sum(1 for d in drift_items if d.severity == "breaking")
    warns = sum(1 for d in drift_items if d.severity == "warning")
    return SchemaDriftResponse(
        has_drift=drift_report.has_drift,
        breaking_changes=breaking,
        warnings=warns,
        drift_items=drift_items,
    )


def _finalize_uploaded_file(
    db: Session,
    file_id,
    original_filename: str,
    stored_path: str,
    file_size_bytes: int,
    request: Request,
    current_user: User,
) -> FileUploadResponse:
    """Persist uploaded file metadata, snapshot, and optional drift report."""
    row_count, columns, dtypes = _extract_file_metadata(original_filename, stored_path)
    if row_count > settings.MAX_ROWS_PER_FILE:
        storage_service.delete(stored_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum rows ({settings.MAX_ROWS_PER_FILE})",
        )

    uploaded_file = UploadedFile(
        id=file_id,
        original_filename=original_filename,
        stored_path=str(stored_path),
        file_size_bytes=file_size_bytes,
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        dtypes=dtypes,
        user_id=current_user.id,
    )
    db.add(uploaded_file)
    db.commit()

    snapshot = SchemaSnapshot(
        file_id=file_id,
        columns=columns,
        dtypes=dtypes,
        row_count=row_count,
    )
    db.add(snapshot)
    db.commit()

    schema_drift_response = _build_schema_drift_response(
        db=db,
        file_id=file_id,
        original_filename=original_filename,
        columns=columns,
        dtypes=dtypes,
    )

    logger.info(
        "File uploaded: id=%s, name=%s, rows=%d, columns=%d",
        file_id,
        original_filename,
        row_count,
        len(columns),
    )

    from backend.metrics import FILES_UPLOADED_TOTAL

    FILES_UPLOADED_TOTAL.inc()
    log_action(
        db,
        "file_uploaded",
        user_id=current_user.id,
        resource_type="file",
        resource_id=file_id,
        details={"filename": original_filename, "row_count": row_count},
        request=request,
    )

    from backend.tasks.profiling import profile_file

    profile_file.apply_async([str(file_id)], queue="bulk")

    return FileUploadResponse(
        id=str(file_id),
        original_filename=original_filename,
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        dtypes=dtypes,
        file_size_bytes=file_size_bytes,
        schema_drift=schema_drift_response,
    )


def _parse_file_preview(filename: str, handle: BinaryIO, nrows: int) -> pd.DataFrame:
    """Parse only the first N rows of a file for preview (avoids reading full file)."""
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".csv":
            return pd.read_csv(handle, nrows=nrows)
        elif extension == ".json":
            df = pd.read_json(handle)
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
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get schema snapshot history for a file."""
    _validate_uuid_format(file_id)
    uploaded_file = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
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
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get schema drift between the two most recent snapshots."""
    _validate_uuid_format(file_id)
    uploaded_file = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
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


@router.get("/{file_id}/profile")
def get_file_profile(
    file_id: str,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get the data profile for an uploaded file."""
    from backend.models import FileProfile
    from sqlalchemy import select

    _validate_uuid_format(file_id)
    file_record = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    result = db.execute(
        select(FileProfile).where(FileProfile.file_id == _as_uuid(file_id))
    )
    profile_record = result.scalar_one_or_none()

    if not profile_record:
        return {
            "file_id": file_id,
            "status": "pending",
            "profile": None,
        }

    return {
        "file_id": file_id,
        "status": profile_record.status,
        "computed_at": profile_record.computed_at.isoformat()
        if profile_record.computed_at
        else None,
        "row_count": profile_record.row_count,
        "col_count": profile_record.col_count,
        "completeness_pct": float(profile_record.completeness_pct)
        if profile_record.completeness_pct
        else None,
        "profile": profile_record.profile,
        "error": profile_record.error,
    }


@router.post("/{file_id}/profile/refresh")
def refresh_file_profile(
    file_id: str,
    db: Session = get_write_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Manually trigger re-profiling of a file."""
    from backend.models import FileProfile
    from sqlalchemy import update

    _validate_uuid_format(file_id)
    file_record = (
        db.query(UploadedFile)
        .filter(
            UploadedFile.id == _as_uuid(file_id),
            UploadedFile.user_id == current_user.id,
        )
        .first()
    )
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    db.execute(
        update(FileProfile)
        .where(FileProfile.file_id == _as_uuid(file_id))
        .values(status="pending", error=None)
    )
    db.commit()

    from backend.tasks.profiling import profile_file

    profile_file.apply_async([file_id], queue="bulk")

    return {"file_id": file_id, "status": "queued"}


# Legacy compatibility endpoints used by Week-1 verification prompt.
legacy_router.add_api_route(
    "/request-upload-url",
    request_upload_url,
    methods=["POST"],
    response_model=UploadUrlResponse,
    summary="Negotiate upload strategy (legacy)",
)
legacy_router.add_api_route(
    "/direct-upload/{file_id}",
    direct_upload_file,
    methods=["PUT"],
    summary="Direct upload target (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}/confirm",
    confirm_direct_upload,
    methods=["POST"],
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Confirm direct upload (legacy)",
)
legacy_router.add_api_route(
    "/upload",
    upload_file,
    methods=["POST"],
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload file (legacy)",
)
legacy_router.add_api_route(
    "/",
    list_files,
    methods=["GET"],
    response_model=FileListResponse,
    summary="List uploaded files (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}",
    get_file,
    methods=["GET"],
    response_model=FileUploadResponse,
    summary="Get file metadata (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}",
    delete_file,
    methods=["DELETE"],
    summary="Delete file (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}/preview",
    preview_file,
    methods=["GET"],
    summary="Preview file data (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}/schema/history",
    get_schema_history,
    methods=["GET"],
    summary="Schema history (legacy)",
)
legacy_router.add_api_route(
    "/{file_id}/schema/diff",
    get_schema_diff,
    methods=["GET"],
    summary="Schema diff (legacy)",
)
