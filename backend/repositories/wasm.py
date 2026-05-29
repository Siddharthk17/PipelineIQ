"""Wasm module persistence helpers.

Synchronous MinIO loaders for use in Celery tasks (where async/await
is not available) plus database lookups for request handlers.
"""

import logging

from sqlalchemy import select, update

from backend.config import settings
from backend.db.minio_client import get_minio_client
from backend.models import WasmModule

logger = logging.getLogger(__name__)

WASM_BUCKET = settings.WASM_BUCKET


def load_wasm_bytes_sync(storage_key: str) -> bytes:
    """Download a Wasm module binary from MinIO synchronously.

    Suitable for Celery task workers where asyncio is not in play.
    Raises minio.error.S3Error on missing or inaccessible objects.
    """
    minio_client = get_minio_client()
    response = minio_client.get_object(WASM_BUCKET, storage_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def get_module_storage_key(module_id: str, user_id: str, db) -> str | None:
    """Return the MinIO storage_key for a validated Wasm module.

    Returns None when the module does not exist, belongs to a different
    user, or has not passed Wasmtime validation.
    """
    result = db.execute(
        select(WasmModule.storage_key)
        .where(WasmModule.id == module_id)
        .where(WasmModule.user_id == user_id)
    )
    row = result.scalar_one_or_none()
    return row


def increment_pipeline_usage(module_id: str, db) -> None:
    """Bump pipeline_usage_count by 1 for the given Wasm module.

    Called when a pipeline YAML is saved or updated with a reference
    to this module.
    """
    db.execute(
        update(WasmModule)
        .where(WasmModule.id == module_id)
        .values(pipeline_usage_count=WasmModule.pipeline_usage_count + 1)
    )


def decrement_pipeline_usage(module_id: str, db) -> None:
    """Reduce pipeline_usage_count by 1, flooring at 0."""
    db.execute(
        update(WasmModule)
        .where(WasmModule.id == module_id)
        .where(WasmModule.pipeline_usage_count > 0)
        .values(pipeline_usage_count=WasmModule.pipeline_usage_count - 1)
    )
