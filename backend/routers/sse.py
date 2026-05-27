"""Compatibility exports for the legacy backend.routers.sse import path.

This module preserves older audit and integration references while routing all
real SSE behavior through backend.api.sse, including heartbeat handling and
stream_end closure on terminal states.
"""

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.api.sse import (
    legacy_router,
    router,
    stream_pipeline_progress as _stream_pipeline_progress,
    stream_pipeline_progress_legacy as _stream_pipeline_progress_legacy,
)
from backend.auth import get_current_user_sse
from backend.dependencies import get_read_db_dependency
from backend.models import User

__all__ = [
    "HEARTBEAT_EVENT",
    "STREAM_END_EVENT",
    "TERMINAL_STATES",
    "legacy_router",
    "router",
    "stream_pipeline_progress",
    "stream_pipeline_progress_legacy",
]

TERMINAL_STATES = ("success", "failed", "healed", "timeout")
STREAM_END_EVENT = "stream_end"
HEARTBEAT_EVENT = "heartbeat"


async def stream_pipeline_progress(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user_sse),
) -> StreamingResponse:
    """Compatibility wrapper for the primary SSE stream endpoint."""
    return await _stream_pipeline_progress(
        run_id=run_id,
        request=request,
        db=db,
        current_user=current_user,
    )


async def stream_pipeline_progress_legacy(
    run_id: str,
    request: Request,
    db: Session = get_read_db_dependency(),
    current_user: User = Depends(get_current_user_sse),
) -> StreamingResponse:
    """Compatibility wrapper for the legacy SSE stream endpoint."""
    return await _stream_pipeline_progress_legacy(
        run_id=run_id,
        request=request,
        db=db,
        current_user=current_user,
    )
