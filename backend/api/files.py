"""File upload and listing API endpoints.

Handles CSV and JSON file uploads with validation for file size,
extension, and content parsing. Uploaded files are stored on disk
with metadata persisted to the database.
"""

import asyncio
import codecs
import contextlib
import csv
import logging
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, BinaryIO, NoReturn, cast

import orjson
import pandas as pd
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from kombu.exceptions import OperationalError as KombuOperationalError
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.db.redis_pools import get_cache_redis
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import SchemaSnapshot, UploadedFile, User
from backend.pipeline.schema_drift import detect_schema_drift
from backend.schemas import (
    ColumnDriftResponse,
    FileListResponse,
    FileUploadResponse,
    SchemaDriftResponse,
    UploadUrlRequest,
    UploadUrlResponse,
)
from backend.services.audit_service import log_action
from backend.services.storage_service import storage_service
from backend.utils.rate_limiter import limiter
from backend.utils.uuid_utils import (
    as_uuid as _as_uuid,
)
from backend.utils.uuid_utils import (
    validate_uuid_format as _validate_uuid_format,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])
legacy_router = APIRouter(prefix="/files", tags=["files-legacy"])

LARGE_FILE_THRESHOLD = 10 * 1024 * 1024
DIRECT_UPLOAD_TTL_SECONDS = 30 * 60

# Bounds for off-loop work (M-103): sync Redis/parse calls must never stall
# the event loop. Redis helpers get a short leash; metadata parsing gets a
# longer one proportional to MAX_UPLOAD_SIZE-bounded inputs.
_REDIS_TIMEOUT_SECONDS = 5.0
_METADATA_TIMEOUT_SECONDS = 30.0


async def _bounded_to_thread(fn, /, *args, timeout: float, service: str):
    """Run sync blocking work off the event loop with a hard timeout.

    Timeouts surface as 503 (degraded, retryable) instead of stalling the
    loop; inner HTTPExceptions (400/415/422) propagate unchanged.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{service} timed out; retry the request",
        ) from exc
MAX_DIRECT_UPLOAD_SIZE = settings.MAX_UPLOAD_SIZE
PENDING_UPLOAD_PREFIX = "files:pending-upload:"
# Multipart framing overhead tolerated when comparing a declared
# Content-Length against the size cap (M-102). A declaration beyond
# cap + slack cannot be a legal upload, so it is rejected before the
# body is consumed.
MULTIPART_OVERHEAD_SLACK = 1024 * 1024


class _UploadTooLargeError(Exception):
    """Raised when a streamed upload exceeds the configured size cap."""


class _CappedReader:
    """File-like proxy enforcing a byte cap on reads (M-102).

    Bounds unbounded upload amplification: the first byte past ``limit``
    aborts the copy so the caller can delete the partial object and
    answer 413. All other attributes delegate to the wrapped stream, so
    it stays compatible with readers that also seek/tell (e.g. S3
    multipart upload).
    """

    def __init__(self, raw: BinaryIO, limit: int) -> None:
        self._raw = raw
        self._limit = limit
        self._total = 0

    def read(self, size: int = -1) -> bytes:
        if self._total > self._limit:
            raise _UploadTooLargeError(
                f"Upload exceeds maximum size ({self._limit} bytes)"
            )
        allowance = self._limit - self._total + 1
        chunk = self._raw.read(
            allowance if size is None or size < 0 else min(size, allowance)
        )
        self._total += len(chunk)
        if self._total > self._limit:
            raise _UploadTooLargeError(
                f"Upload exceeds maximum size ({self._limit} bytes)"
            )
        return chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw, name)


def _declared_content_length(request: Request) -> int | None:
    """Return the request's declared Content-Length, if parseable."""
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _enforce_declared_size(request: Request, *, slack: int = 0) -> None:
    """Reject hopelessly oversized bodies before consuming them (M-102)."""
    declared = _declared_content_length(request)
    if declared is not None and declared > settings.MAX_UPLOAD_SIZE + slack:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum ({settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB)",
        )


def _delete_stored_quietly(stored_path: str) -> None:
    """Best-effort removal of a stored object (never masks the real error)."""
    with contextlib.suppress(Exception):
        storage_service.delete(stored_path)


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
        logger.warning(
            "Failed to cache pending upload state for file_id=%s",
            file_id)


def _get_pending_upload(file_id: str) -> dict | None:
    try:
        raw = cast('str | None', get_cache_redis().get(_pending_upload_key(file_id)))
    except RedisError:
        logger.warning(
            "Failed to read pending upload state for file_id=%s",
            file_id)
        return None
    if not raw:
        return None
    try:
        return orjson.loads(raw)
    except Exception:
        logger.warning(
            "Invalid pending upload payload for file_id=%s",
            file_id)
        return None


def _clear_pending_upload(file_id: str) -> None:
    try:
        get_cache_redis().delete(_pending_upload_key(file_id))
    except RedisError:
        logger.warning(
            "Failed to clear pending upload state for file_id=%s",
            file_id)


def _validated_pending_stored_path(file_id: str, pending: dict) -> str:
    filename = os.path.basename(str(pending.get("filename") or ""))
    _validate_file_extension(filename)
    extension = Path(filename).suffix.lower()
    expected_name = f"{file_id}{extension}"
    actual_name = os.path.basename(str(pending.get("stored_path") or ""))
    if actual_name != expected_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pending upload storage path is invalid",
        )
    return expected_name


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
    body: UploadUrlRequest | None = Body(default=None),
    filename: str | None = Query(default=None),
    file_size: int | None = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
) -> UploadUrlResponse:
    """Negotiate file upload method based on file size."""
    request_payload = _resolve_upload_request_payload(
        body, filename, file_size)

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
    stored_path = f"{file_id}{extension}"

    request_path = request.url.path
    is_legacy_api = request_path.startswith("/api/files/")
    api_prefix = "/api/files" if is_legacy_api else "/api/v1/files"
    absolute_api_prefix = f"{str(request.base_url).rstrip('/')}{api_prefix}"

    if request_payload.file_size > LARGE_FILE_THRESHOLD:
        await _bounded_to_thread(
            _cache_pending_upload,
            file_id,
            {
                "filename": filename,
                "expected_size": request_payload.file_size,
                "stored_path": str(stored_path),
                "user_id": str(current_user.id),
            },
            timeout=_REDIS_TIMEOUT_SECONDS,
            service="Upload state store",
        )

        try:
            presigned_url = storage_service.get_presigned_upload_url(
                str(stored_path), DIRECT_UPLOAD_TTL_SECONDS
            )
        except Exception as exc:
            # R-08: M-115c made presign fail-closed (raises on unreachable
            # bucket instead of returning a dead URL). Negotiation itself must
            # not 500: fall back to the server-proxied upload like the None
            # path below.
            logger.warning(
                "Presigned upload unavailable, using proxied upload: %s", exc)
            presigned_url = None
        if presigned_url is None:
            presigned_url = (
                f"{absolute_api_prefix}/direct-upload/{file_id}"
                if is_legacy_api
                else f"{api_prefix}/direct-upload/{file_id}"
            )

        return UploadUrlResponse(
            method="direct",
            file_id=file_id,
            upload_url=presigned_url,
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
    pending = await _bounded_to_thread(
        _get_pending_upload,
        file_id,
        timeout=_REDIS_TIMEOUT_SECONDS,
        service="Upload state store",
    )
    if pending is None or pending.get("user_id") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not pending confirmation",
        )

    stored_path = Path(_validated_pending_stored_path(file_id, pending))
    expected_size = int(pending["expected_size"])
    _enforce_declared_size(request)
    try:
        written = await storage_service.upload_stream(
            request.stream(), str(stored_path), MAX_DIRECT_UPLOAD_SIZE
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
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
    pending = await _bounded_to_thread(
        _get_pending_upload,
        file_id,
        timeout=_REDIS_TIMEOUT_SECONDS,
        service="Upload state store",
    )
    if pending is None or pending.get("user_id") != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or not pending confirmation",
        )

    stored_path = _validated_pending_stored_path(file_id, pending)
    if not storage_service.exists(stored_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found in storage. Upload may have failed.",
        )

    # M-067: presigned direct-to-S3 uploads bypass the server-side stream
    # cap, so the confirm step is the enforcement point for oversized or
    # empty objects. Reject over-cap (413) and delete the orphaned object.
    actual_size = storage_service.get_size(str(stored_path))
    if actual_size > settings.MAX_UPLOAD_SIZE:
        _delete_stored_quietly(str(stored_path))
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum ({settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB)",
        )
    if actual_size == 0:
        _delete_stored_quietly(str(stored_path))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    finalized = await _finalize_uploaded_file(
        db=db,
        file_id=_as_uuid(file_id),
        original_filename=pending["filename"],
        stored_path=str(stored_path),
        file_size_bytes=actual_size,
        request=request,
        current_user=current_user,
    )
    await _bounded_to_thread(
        _clear_pending_upload,
        file_id,
        timeout=_REDIS_TIMEOUT_SECONDS,
        service="Upload state store",
    )
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
    response: Response,
    file: UploadFile = File(...),
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
    _enforce_declared_size(request, slack=MULTIPART_OVERHEAD_SLACK)

    file_id = uuid.uuid4()
    extension = Path(original_filename).suffix.lower()
    stored_path = f"{file_id}{extension}"

    try:
        storage_service.upload(
            cast(BinaryIO, _CappedReader(file.file, settings.MAX_UPLOAD_SIZE)),
            stored_path,
        )
        written = storage_service.get_size(stored_path)
    except _UploadTooLargeError:
        _delete_stored_quietly(stored_path)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File size exceeds maximum ({settings.MAX_UPLOAD_SIZE // (1024 * 1024)} MB)",
        ) from None
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload file: {exc}",
        ) from exc
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

    return await _finalize_uploaded_file(
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
    page: int = 1,
    limit: int = 50,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user),
) -> FileListResponse:
    """List all uploaded files with their metadata."""
    # M-085: bound the list (catalog default 50 / hard cap 200, clamped
    # like list_pipeline_runs). total stays the full count so small
    # result sets behave exactly as before.
    page = max(1, min(page, 1_000_000))
    limit = max(1, min(limit, 200))
    base_query = db.query(UploadedFile).filter(
        UploadedFile.user_id == current_user.id
    )
    total = base_query.count()
    files = (
        base_query.order_by(UploadedFile.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
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
    return FileListResponse(files=file_responses, total=total)


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
            uploaded_file.original_filename, handle, max(1, min(rows, 100))
        )

    try:
        from backend.security.column_security import (
            apply_column_policies,
            get_column_policies_for_file,
        )

        policies = get_column_policies_for_file(
            file_id=str(uploaded_file.id),
            db=db,
        )
        if policies:
            df = apply_column_policies(df, current_user.role, policies)
    except Exception as exc:
        logger.warning("Column policy enforcement failed for file_id=%s: %s", file_id, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Column security policies could not be applied",
        ) from exc

    return {
        "file_id": file_id,
        "filename": uploaded_file.original_filename,
        "total_rows": uploaded_file.row_count,
        "preview_rows": len(df),
        "columns": list(df.columns),
        "data": _neutralize_formula_cells(df).to_dict(orient="records"),
    }


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_formula_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix spreadsheet-formula cells so previews cannot inject code (M-075).

    Cells whose text starts with ``= + - @`` or tab/CR gain a leading single
    quote, per OWASP CSV-injection guidance. Only string cells are touched;
    numeric dtypes pass through unchanged.
    """

    def _neutralize(value: object) -> object:
        if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
            return "'" + value
        return value

    result = df.copy()
    for column in result.columns:
        series = result[column]
        if series.dtype == object or str(series.dtype) == "string":
            result[column] = series.map(_neutralize)
    return result


def _decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    """Decode CSV bytes, trying BOM-aware and legacy encodings in order.

    Tries UTF-8 (with optional BOM), UTF-16 (with BOM, or with NUL bytes
    present), then Windows-1252. Returns (text, encoding name). Raises
    UnicodeDecodeError when nothing fits, e.g. for binary garbage.
    """
    import codecs

    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16"), "utf-16"
    if b"\x00" in raw:
        try:
            return raw.decode("utf-16"), "utf-16"
        except (UnicodeDecodeError, ValueError):
            pass
    for encoding in ("utf-8-sig", "windows-1252"):
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, ValueError):
            continue
    raise UnicodeDecodeError("csv", raw, 0, 1, "no suitable text encoding")


def _reject_unsupported_xlsx(filename: str) -> NoReturn:
    """Reject .xlsx uploads: openpyxl is not installed (M-066)."""
    raise HTTPException(
        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        detail=(
            f"XLSX files such as '{filename}' are not supported by this "
            "deployment: the optional openpyxl dependency is not installed."
        ),
    )


# UploadedFile.original_filename is String(255): reject overlong basenames
# here so an oversized filename is a clean 400, never a DB overflow 500
# (M-099). The column constraint stays as the backstop.
_MAX_FILENAME_LENGTH = 255


def _validate_filename_length(filename: str) -> None:
    """Raise 400 naming the field if the filename exceeds the DB column."""
    if len(filename) > _MAX_FILENAME_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Field 'filename' is too long ({len(filename)} characters; "
                f"maximum is {_MAX_FILENAME_LENGTH})"
            ),
        )


def _validate_file_extension(filename: str) -> None:
    """Raise 400 if the file extension is not in ALLOWED_EXTENSIONS."""
    _validate_filename_length(filename)
    extension = Path(filename).suffix.lower()
    if extension not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported file extension '{extension}'. "
                f"Allowed: {sorted(settings.ALLOWED_EXTENSIONS)}"
            ),
        )
    if extension == ".xlsx":
        _reject_unsupported_xlsx(filename)


def _validate_file_content(filename: str, stored_path: str) -> None:
    """Perform lightweight content checks before metadata extraction."""
    extension = Path(filename).suffix.lower()
    try:
        with storage_service.download(stored_path) as handle:
            sample = handle.read(1_048_576 if extension in (".csv", ".json") else 4)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not read file '{filename}'",
        ) from exc
    try:
        _check_sample_content(extension, sample)
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"File content does not match extension '{extension}'",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _check_sample_content(extension: str, sample: bytes) -> None:
    """Raise ValueError when a file sample fails its lightweight checks."""
    if extension == ".csv":
        try:
            text, _ = _decode_csv_bytes(sample)
        except UnicodeDecodeError as exc:
            raise ValueError(
                "CSV file is not decodable text "
                "(tried UTF-8, UTF-16 and Windows-1252)"
            ) from exc
        if not next(csv.reader(text.splitlines()), None):
            raise ValueError("CSV file has no header row")
    elif extension == ".json":
        _check_json_sample(sample)
    elif extension == ".parquet" and sample != b"PAR1":
        raise ValueError("Parquet magic header is missing")


_READ_CHUNK_SIZE = 256 * 1024
_JSON_SCHEMA_SAMPLE_VALUES = 100
_JSON_WS = frozenset((0x20, 0x09, 0x0A, 0x0D))
_UTF8_BOM = b"\xef\xbb\xbf"
# Cap a single CSV field so one huge field cannot exhaust memory (M-017),
# and bound the dtype sample to the first records (M-017).
_CSV_MAX_FIELD_BYTES = 16 * 1024 * 1024
_CSV_DTYPE_SAMPLE_RECORDS = 1000


class _MalformedCsvError(ValueError):
    """Raised when CSV text is structurally invalid (e.g. NUL byte)."""


class _JsonMalformedError(ValueError):
    """Raised when streamed JSON is structurally invalid."""


class _JsonTruncatedError(_JsonMalformedError):
    """Raised when input ends mid-value (tolerated by the sniff window)."""


def _iter_json_raw_values(handle: BinaryIO) -> Iterator[bytes]:
    """Yield raw bytes of each top-level JSON record, streaming (M-017/M-038).

    Array mode (first non-whitespace char ``[``): yields each element of the
    single top-level array; anything but whitespace after the closing bracket
    is malformed. Otherwise yields each top-level value in turn (a single
    object, JSONL/whitespace-separated values). Raises _JsonMalformedError on
    unbalanced brackets and _JsonTruncatedError when input ends mid-value.
    Reads the handle in bounded chunks; callers must not reuse the handle.
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
                if closed_top:
                    pass  # trailing whitespace after top-level array
                elif started and depth == (1 if array_mode else 0):
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
        raise _JsonTruncatedError("input ends inside a string")
    if array_mode and not closed_top:
        raise _JsonTruncatedError("input ends inside a top-level array")
    if started:
        if depth == (1 if array_mode else 0):
            yield bytes(data[value_start:])
        else:
            raise _JsonTruncatedError("input ends inside a JSON value")


def _check_json_sample(sample: bytes) -> None:
    """Validate the head of a JSON upload without requiring all of it (M-038)."""
    import io as _io

    nosig = sample[3:] if sample.startswith(_UTF8_BOM) else sample
    if not nosig.lstrip().startswith((b"{", b"[")):
        raise ValueError("JSON file does not start with an object or array")
    try:
        for raw in _iter_json_raw_values(_io.BytesIO(sample)):
            orjson.loads(raw)
            break
    except _JsonTruncatedError:
        pass  # First value extends past the sniff window; metadata validates fully.


def _parse_json_value(raw: bytes, filename: str) -> Any:
    """Parse one streamed JSON record, mapping errors to 422."""
    try:
        return orjson.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': invalid JSON value",
        ) from exc


def _resolve_upload_request_payload(
    body: UploadUrlRequest | None,
    filename: str | None,
    file_size: int | None,
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
    if extension == ".parquet":
        return _extract_parquet_metadata(stored_path)
    if extension == ".xlsx":
        _reject_unsupported_xlsx(filename)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Cannot parse file with extension '{extension}'",
    )


def _detect_csv_encoding(sample: bytes) -> str:
    """Mirror _decode_csv_bytes codec choice from a prefix (M-017)."""
    if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    if b"\x00" in sample:
        try:
            if "\x00" not in sample.decode("utf-16"):
                return "utf-16"
        except (UnicodeDecodeError, ValueError):
            pass
    try:
        sample.decode("utf-8-sig")
    except (UnicodeDecodeError, ValueError):
        return "windows-1252"
    else:
        return "utf-8-sig"


def _iter_csv_text_lines(
    handle: BinaryIO, encoding: str, initial: bytes = b""
) -> Iterator[str]:
    """Yield decoded text lines reading the handle in bounded chunks (M-017)."""
    decoder = codecs.getincrementaldecoder(encoding)()
    buf = ""
    chunk = initial
    first = True
    while True:
        if not first:
            chunk = handle.read(_READ_CHUNK_SIZE)
        first = False
        if not chunk:
            break
        buf += decoder.decode(chunk)
        *lines, buf = buf.split("\n")
        for line in lines:
            if "\x00" in line:
                raise _MalformedCsvError("NUL byte in text content")
            yield line[:-1] if line.endswith("\r") else line
    buf += decoder.decode(b"", final=True)
    if buf:
        if "\x00" in buf:
            raise _MalformedCsvError("NUL byte in text content")
        yield buf[:-1] if buf.endswith("\r") else buf


@contextlib.contextmanager
def _bounded_csv_fields() -> Iterator[None]:
    """Cap single CSV field size so one huge field cannot exhaust memory."""
    previous = csv.field_size_limit(_CSV_MAX_FIELD_BYTES)
    try:
        yield
    finally:
        csv.field_size_limit(previous)


def _extract_csv_metadata(
        stored_path: str) -> tuple[int, list[str], dict[str, str]]:
    """Read CSV metadata streaming row by row with bounded memory (M-017)."""
    import io

    with storage_service.download(stored_path) as handle:
        peek = handle.read(_READ_CHUNK_SIZE)
        if not peek:
            return 0, [], {}
        encoding = _detect_csv_encoding(peek)
        with _bounded_csv_fields():
            reader = csv.reader(
                _iter_csv_text_lines(handle, encoding, initial=peek),
                strict=True,
            )
            try:
                header = next(reader, None)
                if header is None:
                    return 0, [], {}
                columns = [str(column) for column in header]
                sample_records: list[list[str]] = []
                row_count = 0
                capped = False
                for record in reader:
                    row_count += 1
                    if len(sample_records) < _CSV_DTYPE_SAMPLE_RECORDS:
                        sample_records.append(record)
                    if row_count > settings.MAX_ROWS_PER_FILE:
                        capped = True
                        break
            except _MalformedCsvError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Malformed CSV in file '{stored_path}': {exc}",
                ) from exc
            except csv.Error as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Malformed CSV in file '{stored_path}': {exc}",
                ) from exc
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Failed to parse file '{stored_path}': {exc}",
                ) from exc
    if capped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum rows ({settings.MAX_ROWS_PER_FILE})",
        )
    if not sample_records:
        return row_count, columns, {}
    try:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(columns)
        writer.writerows(sample_records)
        buf.seek(0)
        sample_df = pd.read_csv(buf, nrows=_CSV_DTYPE_SAMPLE_RECORDS)
        dtypes = {
            str(column): str(dtype)
            for column, dtype in sample_df.dtypes.items()
        }
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{stored_path}': {exc}",
        ) from exc
    return row_count, columns, dtypes


def _extract_json_metadata(
    filename: str, stored_path: str
) -> tuple[int, list[str], dict[str, str]]:
    """Extract metadata for JSON uploads with bounded memory (M-017/M-038)."""
    try:
        with storage_service.download(stored_path) as handle:
            row_count, sample, capped = _scan_json_records(handle, filename)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc
    if capped:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds maximum rows ({settings.MAX_ROWS_PER_FILE})",
        )
    try:
        sample_df = pd.DataFrame(sample)
        columns = [str(column) for column in sample_df.columns]
        dtypes = {str(col): str(dtype) for col, dtype in sample_df.dtypes.items()}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc
    return row_count, columns, dtypes


def _scan_json_records(
    handle: BinaryIO, filename: str
) -> tuple[int, list[Any], bool]:
    """Stream JSON records; return (row_count, schema sample, row_capped)."""
    records = _iter_json_raw_values(handle)
    try:
        first_raw = next(records, None)
    except _JsonMalformedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc
    if first_raw is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': empty file",
        )
    sample = [_parse_json_value(first_raw, filename)]
    row_count = 1
    capped = False
    try:
        for raw in records:
            value = _parse_json_value(raw, filename)
            row_count += 1
            if len(sample) < _JSON_SCHEMA_SAMPLE_VALUES:
                sample.append(value)
            if row_count > settings.MAX_ROWS_PER_FILE:
                capped = True
                break
    except _JsonMalformedError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc
    return row_count, sample, capped


def _extract_parquet_metadata(stored_path: str) -> tuple[int, list[str], dict[str, str]]:
    try:
        import pyarrow.parquet as pq

        with storage_service.download(stored_path) as handle:
            parquet_file = pq.ParquetFile(handle)
            schema = parquet_file.schema_arrow
            columns = list(schema.names)
            dtypes = {field.name: str(field.type) for field in schema}
            return parquet_file.metadata.num_rows, columns, dtypes
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse parquet file '{stored_path}': {exc}",
        ) from exc


def _build_schema_drift_response(
    db: Session,
    file_id,
    original_filename: str,
    columns: list[str],
    dtypes: dict,
    user_id,
) -> SchemaDriftResponse | None:
    """Compare against the uploader's own previous snapshot with the same name.

    The baseline lookup is scoped to (owner, filename): snapshots uploaded
    by other tenants with the same ``original_filename`` must never leak
    their column names/dtypes into this response.
    """
    drift_report = None
    previous_snapshot = (
        db.query(SchemaSnapshot)
        .join(UploadedFile, SchemaSnapshot.file_id == UploadedFile.id)
        .filter(
            UploadedFile.original_filename == original_filename,
            UploadedFile.user_id == user_id,
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


def _enqueue_profile_task(file_id: str) -> None:
    """Queue async profiling without failing requests when broker is unavailable."""
    from backend.tasks.profiling import profile_file

    try:
        profile_file.apply_async([str(file_id)], queue="bulk")
    except (KombuOperationalError, RedisError, OSError, RuntimeError) as exc:
        logger.warning(
            "Profile task enqueue failed for file_id=%s; continuing without async profiling: %s",
            file_id,
            exc,
        )


async def _finalize_uploaded_file(
    db: Session,
    file_id,
    original_filename: str,
    stored_path: str,
    file_size_bytes: int,
    request: Request,
    current_user: User,
) -> FileUploadResponse:
    """Persist uploaded file metadata, snapshot, and optional drift report."""
    try:
        _validate_file_content(original_filename, stored_path)
        row_count, columns, dtypes = await _bounded_to_thread(
            _extract_file_metadata,
            original_filename,
            stored_path,
            timeout=_METADATA_TIMEOUT_SECONDS,
            service="File metadata extraction",
        )
    except HTTPException:
        # Never orphan the stored object when validation/metadata fails (M-101).
        _delete_stored_quietly(str(stored_path))
        raise
    if row_count > settings.MAX_ROWS_PER_FILE:
        _delete_stored_quietly(str(stored_path))
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
        user_id=current_user.id,
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

    _enqueue_profile_task(str(file_id))

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


def _unsupported_preview_extension(extension: str) -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Cannot parse file with extension '{extension}'",
    )


def _parse_file_preview(
        filename: str,
        handle: BinaryIO,
        nrows: int) -> pd.DataFrame:
    """Parse only the first N rows of a file for preview (bounded reads)."""
    extension = Path(filename).suffix.lower()
    try:
        if extension == ".csv":
            return _preview_csv(handle, nrows)
        elif extension == ".json":
            return _preview_json(filename, handle, nrows)
        elif extension == ".parquet":
            return _preview_parquet(handle, nrows)
        elif extension == ".xlsx":
            _reject_unsupported_xlsx(filename)
        else:
            _unsupported_preview_extension(extension)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file '{filename}': {exc}",
        ) from exc


def _preview_csv(handle: BinaryIO, nrows: int) -> pd.DataFrame:
    """Read only the first nrows of a CSV with bounded memory."""
    import io

    peek = handle.read(_READ_CHUNK_SIZE)
    if not peek:
        return pd.DataFrame()
    encoding = _detect_csv_encoding(peek)
    with _bounded_csv_fields():
        reader = csv.reader(
            _iter_csv_text_lines(handle, encoding, initial=peek),
            strict=True,
        )
        header = next(reader, None)
        if header is None:
            return pd.DataFrame()
        records = []
        for record in reader:
            records.append(record)
            if len(records) >= nrows:
                break
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    writer.writerows(records)
    buf.seek(0)
    return pd.read_csv(buf, nrows=nrows)


def _preview_json(
        filename: str, handle: BinaryIO, nrows: int) -> pd.DataFrame:
    """Read only the first nrows JSON records with bounded memory."""
    records = []
    for raw in _iter_json_raw_values(handle):
        records.append(_parse_json_value(raw, filename))
        if len(records) >= nrows:
            break
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _preview_parquet(handle: BinaryIO, nrows: int) -> pd.DataFrame:
    """Read only the first nrows of a parquet file via row groups."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    parquet_file = pq.ParquetFile(handle)
    tables = []
    rows = 0
    for index in range(parquet_file.num_row_groups):
        table = parquet_file.read_row_group(index)
        take = min(table.num_rows, nrows - rows)
        if take > 0:
            tables.append(table.slice(0, take))
            rows += take
        if rows >= nrows:
            break
    if not tables:
        return pd.DataFrame()
    return pa.concat_tables(tables).to_pandas()


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
    from sqlalchemy import select

    from backend.models import FileProfile

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
    from sqlalchemy import update

    from backend.models import FileProfile

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

    _enqueue_profile_task(file_id)

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
