"""WebAssembly module management API endpoints.

Handles upload, validation, listing, and deletion of Wasm UDF modules.
Modules are stored in MinIO/S3 with metadata tracked in the database.
"""

import asyncio
import hashlib
import logging
import uuid
from io import BytesIO

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from minio import Minio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.config import settings
from backend.db.minio_client import get_minio_client
from backend.dependencies import get_read_db_dependency, get_write_db_dependency
from backend.models import User, WasmModule
from backend.repositories.wasm import load_wasm_bytes_sync
from backend.schemas import (
    WasmModuleExport,
    WasmModuleListResponse,
    WasmModuleUploadResponse,
    WasmModuleValidateResponse,
)
from backend.utils.rate_limiter import limiter
from backend.utils.uuid_utils import as_uuid

router = APIRouter(prefix="/api/v1/wasm", tags=["Wasm Modules"])
logger = logging.getLogger(__name__)

MAX_WASM_SIZE = 10 * 1024 * 1024  # 10 MB

# M-037: bound attacker-controlled compilation. Size is checked before
# compile; compile itself runs off the event loop with a wall-clock cap
# so a pathological module cannot stall a server worker (~12s observed).
WASM_COMPILE_TIMEOUT_SECONDS = 20.0

# M-040/M-154: module-metadata bounds.
MIN_FUEL_BUDGET = 1_000
MAX_FUEL_BUDGET = 100_000_000
DEFAULT_FUEL_BUDGET = 10_000_000


def get_owned_wasm_module(db: Session, module_id: str, owner_id: object) -> WasmModule:
    """Return the Wasm module iff it belongs to ``owner_id``.

    Raises 404 both when the module does not exist and when it belongs
    to a different user, so callers cannot probe other users' module ids
    (no ownership oracle). Pipeline-save validation and run-start loading
    (outside this router) must resolve every referenced ``wasm_file_id``
    through this helper — or :func:`assert_wasm_ids_owned` — so a
    pipeline can never load or execute another user's module (M-077).
    """
    module = (
        db.query(WasmModule)
        .filter(
            WasmModule.id == as_uuid(module_id),
            WasmModule.user_id == as_uuid(str(owner_id)),
        )
        .first()
    )

    if not module:
        raise HTTPException(status_code=404, detail="Wasm module not found")
    return module


def assert_wasm_ids_owned(
    db: Session, wasm_ids: set[str] | list[str], owner_id: object
) -> dict[str, WasmModule]:
    """Validate that every referenced module belongs to ``owner_id``.

    Returns a mapping of wasm id string to module. Raises 404 naming the
    first missing/foreign id. Use at pipeline save time (reject
    cross-owner references) and at run start (load only owned modules).
    """
    resolved: dict[str, WasmModule] = {}
    for wid in dict.fromkeys(str(w) for w in wasm_ids):
        resolved[wid] = get_owned_wasm_module(db, wid, owner_id)
    return resolved


def extract_wasm_ids_from_yaml(yaml_text: str) -> set[str]:
    """Return every ``wasm_file_id`` referenced by a pipeline YAML (M-077).

    Unparseable YAML yields an empty set — YAML validity itself is enforced
    by the pipeline parser; this helper only collects references.
    """
    try:
        import yaml as _yaml

        document = _yaml.safe_load(yaml_text)
    except Exception:
        return set()
    if not isinstance(document, dict):
        return set()
    pipeline = document.get("pipeline", document)
    if not isinstance(pipeline, dict):
        return set()
    steps = pipeline.get("steps", [])
    if not isinstance(steps, list):
        return set()
    ids: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        wid = step.get("wasm_file_id")
        if isinstance(wid, str) and wid.strip():
            wid = wid.strip()
        elif wid is not None:
            wid = str(wid)
        else:
            continue
        if wid:
            ids.add(wid)
    return ids


def validate_pipeline_wasm_ownership(
    db: Session, yaml_text: str, owner_id: object
) -> dict[str, WasmModule]:
    """Pipeline-save gate (M-077): every ``wasm_file_id`` in the YAML must
    belong to ``owner_id``.

    Returns the owned id -> module mapping. Raises 404 (no oracle:
    missing and foreign ids are indistinguishable) on the first
    violation, so a pipeline can never be saved referencing another
    user's module.
    """
    return assert_wasm_ids_owned(db, extract_wasm_ids_from_yaml(yaml_text), owner_id)


def load_owned_wasm_modules_for_run(
    db: Session, wasm_ids: set[str] | list[str], owner_id: object
) -> dict[str, bytes]:
    """Run-start loader (M-077): owner-filtered Wasm bytes for execution.

    Ownership is resolved first via :func:`assert_wasm_ids_owned` (404,
    no oracle), then each module binary is fetched from storage. Storage
    failures raise 502 — the module exists but its bytes are unavailable.
    """
    resolved = assert_wasm_ids_owned(db, wasm_ids, owner_id)
    loaded: dict[str, bytes] = {}
    for wid, module in resolved.items():
        try:
            loaded[wid] = load_wasm_bytes_sync(module.storage_key)
        except Exception as exc:
            logger.warning(
                "Failed to load Wasm module bytes for run: id=%s, key=%s",
                wid,
                module.storage_key,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Wasm module storage unavailable. Please try again later.",
            ) from exc
    return loaded


def _ensure_wasm_bucket(minio_client: Minio) -> None:
    """Create the Wasm bucket if it doesn't exist."""
    if not minio_client.bucket_exists(settings.WASM_BUCKET):
        minio_client.make_bucket(settings.WASM_BUCKET)


async def _inspect_wasm_async(wasm_bytes: bytes) -> tuple[list[dict], list[str]]:
    """Compile + inspect a Wasm binary off the event loop (M-037).

    Compilation of attacker-controlled bytes is CPU-heavy: it runs in a
    worker thread and is bounded by WASM_COMPILE_TIMEOUT_SECONDS so one
    upload cannot stall a server worker. Raises TimeoutError on expiry,
    ValueError on invalid binaries.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_inspect_wasm, wasm_bytes),
            timeout=WASM_COMPILE_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            f"Wasm compilation timed out after "
            f"{WASM_COMPILE_TIMEOUT_SECONDS:g}s; module is too complex"
        ) from exc


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
            exports.append(
                {
                    "name": export.name,
                    "params": params,
                    "result": results[0] if results else None,
                }
            )

    imports = []
    for import_ in module.imports:
        imports.append(import_.module)

    return exports, imports


@router.post("/validate", response_model=WasmModuleValidateResponse)
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def validate_wasm_module(
    request: Request,
    response: Response,
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
            detail=f"Wasm module exceeds {MAX_WASM_SIZE // (1024 * 1024)}MB limit",
        )

    errors: list[str] = []
    warnings: list[str] = []
    exports: list[dict] = []
    imports: list[str] = []

    try:
        exports, imports = await _inspect_wasm_async(wasm_bytes)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
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
@limiter.limit(settings.RATE_LIMIT_FILE_UPLOAD)
async def upload_wasm_module(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    # M-040: the UI posts these as multipart form fields (see
    # frontend/lib/api.ts uploadWasmModule). Accept form fields, falling
    # back to query params for backwards compatibility.
    name: str | None = Form(default=None),
    description: str | None = Form(default=None),
    fuel_budget: int | None = Form(default=None),
    db: Session = Depends(get_write_db_dependency),
    current_user: User = Depends(get_current_user),
):
    """Upload and register a Wasm UDF module.

    The module is validated, stored in MinIO/S3, and registered in the database.
    """
    query = request.query_params
    if name is None:
        name = query.get("name")
    if description is None:
        description = query.get("description")
    if fuel_budget is None:
        raw_budget = query.get("fuel_budget")
        if raw_budget is not None:
            try:
                fuel_budget = int(raw_budget)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="fuel_budget must be an integer",
                ) from exc
    if fuel_budget is None:
        fuel_budget = DEFAULT_FUEL_BUDGET

    if not name or len(name) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Module name must be at least 2 characters",
        )
    if fuel_budget < MIN_FUEL_BUDGET or fuel_budget > MAX_FUEL_BUDGET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"fuel_budget must be between {MIN_FUEL_BUDGET} and {MAX_FUEL_BUDGET}"),
        )

    wasm_bytes = await file.read()

    if len(wasm_bytes) > MAX_WASM_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Wasm module exceeds {MAX_WASM_SIZE // (1024 * 1024)}MB limit",
        )

    try:
        exports, imports = await _inspect_wasm_async(wasm_bytes)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid Wasm binary: {exc}",
        ) from exc

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

    existing = (
        db.query(WasmModule)
        .filter(
            WasmModule.name == name,
            WasmModule.user_id == current_user.id,
        )
        .first()
    )

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
    try:
        db.commit()
    except IntegrityError as exc:
        # M-154: WasmModule.name is globally UNIQUE in the DB while the
        # application check above is per-user. A cross-user collision must
        # not surface as a 500 or leak who owns the name: report a plain
        # 409 scoped to the caller's library. (True per-user namespaces
        # need a composite (user_id, name) migration — see REPORT.md.)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A module named '{name}' already exists. "
            "Delete it first or use a different name.",
        ) from exc
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
    module = get_owned_wasm_module(db, module_id, current_user.id)

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
    module = get_owned_wasm_module(db, module_id, current_user.id)

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
