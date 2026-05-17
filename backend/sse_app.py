"""Dedicated FastAPI app for Server-Sent Events streaming endpoints."""

import logging

from fastapi import FastAPI

from backend.api.sse import router as sse_router, legacy_router as sse_legacy_router


class _SSEHealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg and "/livez" not in msg and "/readyz" not in msg


logging.getLogger("uvicorn.access").addFilter(_SSEHealthCheckFilter())
logging.getLogger("gunicorn.access").addFilter(_SSEHealthCheckFilter())


sse_app = FastAPI(
    title="PipelineIQ SSE Service",
    description="Dedicated service for Server-Sent Events streaming",
    docs_url=None,
    redoc_url=None,
)

sse_app.include_router(sse_router, prefix="/api/v1")
sse_app.include_router(sse_router, prefix="/api")
sse_app.include_router(sse_legacy_router, prefix="/api/v1")
sse_app.include_router(sse_legacy_router, prefix="/api")


@sse_app.api_route("/health", methods=["GET", "HEAD"])
def sse_health() -> dict:
    """Liveness probe endpoint for the dedicated SSE service."""
    return {"status": "ok", "service": "sse"}
