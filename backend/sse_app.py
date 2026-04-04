"""Dedicated FastAPI app for Server-Sent Events streaming endpoints."""

from fastapi import FastAPI

from backend.api.sse import router as sse_router, legacy_router as sse_legacy_router


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
