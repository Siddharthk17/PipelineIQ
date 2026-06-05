"""WebAssembly module management API endpoints.

Handles upload, validation, listing, and deletion of Wasm UDF modules.
Modules are stored in MinIO/S3 with metadata tracked in the database.
"""

import hashlib
import logging
import uuid
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from minio import Minio
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.db.minio_client import get_minio_client
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User, WasmModule
from backend.schemas import (
    WasmModuleExport,
    WasmModuleListResponse,
    WasmModuleUploadResponse,
    WasmModuleValidateResponse,
)
from backend.utils.uuid_utils import as_uuid

router = APIRouter(prefix="/api/v1/wasm", tags=["Wasm Modules"])
logger = logging.getLogger(__name__)

MAX_WASM_SIZE = 10 * 1024 * 1024  # 10 MB


def _ensure_wasm_bucket(minio_client: Minio) -> None:
    """Create the Wasm bucket if it doesn't exist."""
    if not minio_client.bucket_exists(settings.WASM_BUCKET):
        minio_client.make_bucket(settings.WASM_BUCKET)


def _inspect_wasm(wasm_bytes: bytes) -> tuple[list[dict], list[str]]:
    """Inspect a Wasm binary to extract exports and imports.

    Returns (exports, imports) where exports is a list of dicts with
    name/params/result keys and imports is a list of module names.
    """
    from wasmtime import Engine, Module

    engine = Engine()
    try:
        module = Module(engine, wasm_bytes)
    except Exception as exc:
        raise ValueError(f"Invalid Wasm binary: {exc}") from exc

    exports = []
    for export in module.exports:
        export_type = export.type
        if hasattr(export_type, "params") and hasattr(export_type, "results"):
            params = [str(p) for p in export_type.params]
            results = [str(r) for r in export_type.results]
            exports.append({
                "name": export.name,
                "params": params,
                "result": results[0] if results else None,
            })

    imports = []
    for import_ in module.imports:
        imports.append(import_.module)

    return exports, imports


@router.post("/validate", response_model=WasmModuleValidateResponse)
async def validate_wasm_module(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Validate a Wasm module without registering it.

    Checks: valid binary, no imports (sandbox), at least one export,
    all exports are callable functions.
    """
    wasm_bytes = await file.read()

    if len(wasm_bytes) > MAX_WASM_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wasm module exceeds {MAX_WASM_SIZE // (1024*1024)}MB limit",
        )

    errors = []
    warnings = []
    exports = []
    imports = []

    try:
        exports, imports = _inspect_wasm(wasm_bytes)
    except ValueError as exc:
        errors.append(str(exc))
        return WasmModuleValidateResponse(
            is_valid=False, exports=[], imports=[], errors=errors, warnings=warnings
        )

    if imports:
        errors.append(
            f"Module imports from external modules: {', '.join(set(imports))}. "
            "Wasm UDFs must be self-contained (no WASI, no external imports)."
        )

    if not exports:
        errors.append("Module has no exported functions.")

    non_func_exports = []
    for exp in exports:
        if exp.get("result") is None and exp.get("params"):
            non_func_exports.append(exp["name"])

    if non_func_exports:
        warnings.append(
            f"Non-function exports detected: {', '.join(non_func_exports)}. "
            "Only callable functions are supported as UDFs."
        )

    return WasmModuleValidateResponse(
        is_valid=len(errors) == 0,
        exports=[WasmModuleExport(**e) for e in exports],
        imports=list(set(imports)),
        errors=errors,
        warnings=warnings,
    )


@router.post("/upload", response_model=WasmModuleUploadResponse)
async def upload_wasm_module(
    name: str,
    file: UploadFile = File(...),
    description: Optional[str] = None,
    fuel_budget: int = 10_000_000,
    db: Session = Depends(get_write_db_dependency),
    current_user: User = Depends(get_current_user),
):
    """Upload and register a Wasm UDF module.

    The module is validated, stored in MinIO/S3, and registered in the database.
    """
    if not name or len(name) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Module name must be at least 2 characters",
        )

    wasm_bytes = await file.read()

    if len(wasm_bytes) > MAX_WASM_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wasm module exceeds {MAX_WASM_SIZE // (1024*1024)}MB limit",
        )

    exports, imports = _inspect_wasm(wasm_bytes)

    if imports:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Module has external imports: {', '.join(set(imports))}. "
            "Wasm UDFs must be self-contained.",
        )

    if not exports:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Module has no exported functions.",
        )

    sha256_hash = hashlib.sha256(wasm_bytes).hexdigest()
    file_size = len(wasm_bytes)

    existing = db.query(WasmModule).filter(
        WasmModule.name == name,
        WasmModule.user_id == current_user.id,
    ).first()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A module named '{name}' already exists. "
            "Delete it first or use a different name.",
        )

    storage_key = f"modules/{uuid.uuid4()}.wasm"

    minio_client = get_minio_client()
    _ensure_wasm_bucket(minio_client)

    minio_client.put_object(
        settings.WASM_BUCKET,
        storage_key,
        BytesIO(wasm_bytes),
        length=file_size,
        content_type="application/wasm",
    )

    module = WasmModule(
        name=name,
        description=description,
        storage_key=storage_key,
        file_size_bytes=file_size,
        sha256_hash=sha256_hash,
        exports=exports,
        imports=[],
        fuel_budget=fuel_budget,
        user_id=current_user.id,
    )
    db.add(module)
    db.commit()
    db.refresh(module)

    logger.info(
        "Wasm module uploaded: name=%s, id=%s, user=%s",
        name,
        module.id,
        current_user.id,
    )

    return WasmModuleUploadResponse(
        id=str(module.id),
        name=module.name,
        description=module.description,
        file_size_bytes=module.file_size_bytes,
        sha256_hash=module.sha256_hash,
        exports=[WasmModuleExport(**e) for e in module.exports],
        imports=module.imports,
        fuel_budget=module.fuel_budget,
        is_active=module.is_active,
        created_at=module.created_at,
    )


@router.get("/", response_model=WasmModuleListResponse)
async def list_wasm_modules(
    db: Session = Depends(get_read_db_dependency),
    current_user: User = Depends(get_current_user),
):
    """List all Wasm modules registered by the current user."""
    modules = (
        db.query(WasmModule)
        .filter(WasmModule.user_id == current_user.id)
        .order_by(WasmModule.created_at.desc())
        .all()
    )

    return WasmModuleListResponse(
        modules=[
            WasmModuleUploadResponse(
                id=str(m.id),
                name=m.name,
                description=m.description,
                file_size_bytes=m.file_size_bytes,
                sha256_hash=m.sha256_hash,
                exports=[WasmModuleExport(**e) for e in m.exports],
                imports=m.imports,
                fuel_budget=m.fuel_budget,
                is_active=m.is_active,
                created_at=m.created_at,
            )
            for m in modules
        ],
        total=len(modules),
    )


@router.get("/{module_id}", response_model=WasmModuleUploadResponse)
async def get_wasm_module(
    module_id: str,
    db: Session = Depends(get_read_db_dependency),
    current_user: User = Depends(get_current_user),
):
    """Get details of a specific Wasm module."""
    module = db.query(WasmModule).filter(
        WasmModule.id == as_uuid(module_id),
        WasmModule.user_id == current_user.id,
    ).first()

    if not module:
        raise HTTPException(status_code=404, detail="Wasm module not found")

    return WasmModuleUploadResponse(
        id=str(module.id),
        name=module.name,
        description=module.description,
        file_size_bytes=module.file_size_bytes,
        sha256_hash=module.sha256_hash,
        exports=[WasmModuleExport(**e) for e in module.exports],
        imports=module.imports,
        fuel_budget=module.fuel_budget,
        is_active=module.is_active,
        created_at=module.created_at,
    )


@router.delete("/{module_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_wasm_module(
    module_id: str,
    db: Session = Depends(get_write_db_dependency),
    current_user: User = Depends(get_current_user),
):
    """Delete a Wasm module and remove it from storage."""
    module = db.query(WasmModule).filter(
        WasmModule.id == as_uuid(module_id),
        WasmModule.user_id == current_user.id,
    ).first()

    if not module:
        raise HTTPException(status_code=404, detail="Wasm module not found")

    if module.pipeline_usage_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete: module is referenced by "
                f"{module.pipeline_usage_count} pipeline(s). "
                f"Remove all wasm_compute steps referencing this module first."
            ),
        )

    minio_client = get_minio_client()
    try:
        minio_client.remove_object(settings.WASM_BUCKET, module.storage_key)
    except Exception as exc:
        logger.warning("Failed to remove Wasm module from storage: %s", exc)

    db.delete(module)
    db.commit()

    logger.info(
        "Wasm module deleted: name=%s, id=%s, user=%s",
        module.name,
        module.id,
        current_user.id,
    )
