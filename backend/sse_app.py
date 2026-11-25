"""Dedicated FastAPI app for Server-Sent Events streaming endpoints."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.api.sse import legacy_router as sse_legacy_router
from backend.api.sse import router as sse_router

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# M-169: shutdown grace budget (seconds). Must fit inside the k8s
# terminationGraceSeconds window (see docs/sse-streaming.md) so SIGTERM
# drains in-flight streams instead of hanging until SIGKILL. Lifespan
# shutdown waits at most this long for stream/pool cleanup.
SSE_SHUTDOWN_TIMEOUT_SECONDS = _env_float("SSE_SHUTDOWN_TIMEOUT_SECONDS", 10.0)


class _SSEHealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/health" not in msg and "/livez" not in msg and "/readyz" not in msg


logging.getLogger("uvicorn.access").addFilter(_SSEHealthCheckFilter())
logging.getLogger("gunicorn.access").addFilter(_SSEHealthCheckFilter())


@asynccontextmanager
async def sse_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup/shutdown for the SSE service (M-169, M-170).

    Startup fail-fasts with a clear message when PostgreSQL is
    unreachable (no silent boot into a broken service). Shutdown closes
    DB engines and Redis pools inside a grace budget so SIGTERM drains
    instead of hanging on leaked connections.
    """
    logger.info("SSE service starting")
    from backend.auth import require_explicit_signing_key

    # Same split-brain guard as the API lifespan: never stream on an
    # ephemeral per-process signing key.
    require_explicit_signing_key()
    _fail_fast_on_pg_unreachable()
    logger.info("SSE service startup complete")
    try:
        yield
    finally:
        logger.info("SSE service shutting down")
        try:
            await asyncio.wait_for(
                _close_sse_resources(),
                timeout=SSE_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "SSE shutdown exceeded %.0fs grace budget; "
                "continuing with forced close",
                SSE_SHUTDOWN_TIMEOUT_SECONDS,
            )
            await _close_sse_resources()
        logger.info("SSE service shutdown complete")


def _fail_fast_on_pg_unreachable() -> None:
    """Raise a clear error when PostgreSQL is unreachable (M-170).

    Skipped for non-Postgres URLs (sqlite test/dev) so unit tests and
    local sqlite runs boot without a database server.
    """
    from sqlalchemy import text

    from backend.database import write_engine

    url = str(write_engine.url)
    if "postgresql" not in url:
        logger.info("SSE startup: non-Postgres DB URL, skipping PG check")
        return
    try:
        with write_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        message = (
            "SSE service cannot start: PostgreSQL unreachable "
            f"(DATABASE_WRITE_URL host check failed: {exc}). "
            "Refusing to boot into a broken service; fix the database "
            "and restart."
        )
        logger.exception(message)
        raise RuntimeError(message) from exc
    logger.info("SSE startup: PostgreSQL reachable")


async def _close_sse_resources() -> None:
    """Dispose DB engines and disconnect Redis pools (M-169)."""
    try:
        from backend.database import read_engine, write_engine
        if read_engine is write_engine:
            write_engine.dispose()
        else:
            read_engine.dispose()
            write_engine.dispose()
        logger.info("SSE shutdown: DB engines disposed")
    except Exception as exc:
        logger.warning("SSE shutdown: engine dispose failed: %s", exc)
    try:
        from backend.db import redis_pools as pools
        for pool in (
            getattr(pools, "_broker_pool", None),
            getattr(pools, "_pubsub_pool", None),
            getattr(pools, "_cache_pool", None),
            getattr(pools, "_cache_binary_pool", None),
            getattr(pools, "_yjs_pool", None),
        ):
            if pool is not None:
                try:
                    pool.disconnect()
                except Exception as exc:
                    logger.warning(
                        "SSE shutdown: sync pool disconnect failed: %s", exc)
        for pool in (
            getattr(pools, "_pubsub_async_pool", None),
            getattr(pools, "_cache_async_pool", None),
        ):
            if pool is not None:
                try:
                    await pool.disconnect()
                except Exception as exc:
                    logger.warning(
                        "SSE shutdown: async pool disconnect failed: %s",
                        exc,
                    )
        logger.info("SSE shutdown: Redis pools disconnected")
    except Exception as exc:
        logger.warning("SSE shutdown: redis cleanup failed: %s", exc)


sse_app = FastAPI(
    title="PipelineIQ SSE Service",
    description="Dedicated service for Server-Sent Events streaming",
    docs_url=None,
    redoc_url=None,
    lifespan=sse_lifespan,
)

sse_app.include_router(sse_router, prefix="/api/v1")
sse_app.include_router(sse_router, prefix="/api")
sse_app.include_router(sse_legacy_router, prefix="/api/v1")
sse_app.include_router(sse_legacy_router, prefix="/api")


@sse_app.api_route("/health", methods=["GET", "HEAD"])
def sse_health() -> dict:
    """Liveness probe endpoint for the dedicated SSE service."""
    return {"status": "ok", "service": "sse"}


@sse_app.get("/livez", include_in_schema=False)
def sse_livez() -> dict:
    """Lightweight liveness probe — no dependency calls."""
    return {"status": "ok", "service": "sse"}


@sse_app.get("/readyz", include_in_schema=False)
def sse_readyz():
    """Readiness probe (M-170): 200 only when PG + Redis answer.

    Returns 503 ``{"status": "degraded", ...}`` when a dependency is
    down — honest degraded signal for load balancers instead of a 200
    that hides the outage.
    """
    checks: dict[str, str] = {}
    try:
        from sqlalchemy import text

        from backend.database import write_engine
        with write_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("SSE readyz degraded: db unreachable: %s", exc)
        checks["db"] = "error"
    try:
        from backend.db.redis_pools import (
            get_cache_redis,
            get_pubsub_redis,
        )
        get_pubsub_redis().ping()
        get_cache_redis().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("SSE readyz degraded: redis unreachable: %s", exc)
        checks["redis"] = "error"

    if all(value == "ok" for value in checks.values()):
        return {"status": "ok", "service": "sse", "checks": checks}
    return JSONResponse(
        status_code=503,
        content={"status": "degraded", "service": "sse", "checks": checks},
    )
