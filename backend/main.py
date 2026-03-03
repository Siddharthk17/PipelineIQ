"""FastAPI application factory for PipelineIQ.

Configures the application with lifespan management, exception handlers,
middleware, route mounting, and health checks. This is the single
entry point for the application.
"""

# Standard library
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

# Third-party packages
import redis
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Internal modules
from backend.api.router import api_router
from backend.config import settings
from backend.database import create_all_tables, engine
from backend.pipeline.exceptions import PipelineIQError

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# LIFESPAN
# ═══════════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup and shutdown.

    Startup:
        - Creates database tables
        - Validates upload directory exists and is writable
        - Logs application start with version and config summary

    Shutdown:
        - Logs clean application shutdown
    """
    # Startup
    logger.info(
        "Starting %s v%s (debug=%s, log_level=%s)",
        settings.APP_NAME, settings.APP_VERSION,
        settings.DEBUG, settings.LOG_LEVEL,
    )
    create_all_tables()
    _validate_upload_dir()
    logger.info("Application startup complete")

    yield

    # Shutdown
    logger.info("Shutting down %s", settings.APP_NAME)
    engine.dispose()
    logger.info("Application shutdown complete")


def _validate_upload_dir() -> None:
    """Ensure the upload directory exists and is writable."""
    upload_dir = settings.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    test_file = upload_dir / ".write_test"
    try:
        test_file.touch()
        test_file.unlink()
        logger.info("Upload directory validated: %s", upload_dir)
    except OSError as exc:
        logger.error(
            "Upload directory '%s' is not writable: %s", upload_dir, exc
        )
        raise


# ═══════════════════════════════════════════════════════════════════════════════
# APPLICATION
# ═══════════════════════════════════════════════════════════════════════════════


app = FastAPI(
    title="PipelineIQ API",
    description=(
        "Data pipeline orchestration engine with automatic column-level "
        "lineage tracking. Define transformation pipelines in YAML, execute "
        "them, and visualize every column's journey as a directed graph."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    contact={
        "name": "PipelineIQ Team",
        "url": "https://github.com/pipelineiq",
    },
)


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTION HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════


@app.exception_handler(PipelineIQError)
async def pipelineiq_error_handler(
    request: Request, exc: PipelineIQError
) -> JSONResponse:
    """Handle PipelineIQ domain errors with structured error bodies."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning(
        "Domain error (request_id=%s): %s", request_id, exc.message
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_type": exc.__class__.__name__,
            "message": exc.message,
            "details": exc.to_dict(),
            "request_id": request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with field-level details."""
    request_id = getattr(request.state, "request_id", "unknown")
    # Sanitize errors: Pydantic V2 may include non-serializable ctx values
    safe_errors = []
    for err in exc.errors():
        safe_err = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            safe_err["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        safe_errors.append(safe_err)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error_type": "ValidationError",
            "message": "Request validation failed",
            "details": safe_errors,
            "request_id": request_id,
        },
    )


@app.exception_handler(Exception)
async def generic_error_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle unexpected errors with a safe error response.

    Never exposes internal details to the client. The request_id
    can be used to find the full traceback in the server logs.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "Unhandled error (request_id=%s): %s",
        request_id, exc, exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_type": "InternalServerError",
            "message": "An unexpected error occurred. Check server logs.",
            "request_id": request_id,
        },
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MIDDLEWARE
# ═══════════════════════════════════════════════════════════════════════════════


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Add a unique X-Request-ID header to every request and response."""
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Add X-Process-Time header with request processing duration."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════


app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get(
    "/health",
    response_model=None,
    summary="Health check",
    description="Checks actual DB and Redis connectivity.",
)
def health_check() -> dict:
    """Health check endpoint that verifies DB and Redis connectivity.

    Returns:
        Dictionary with status, version, and connectivity checks.
    """
    db_status = _check_db_health()
    redis_status = _check_redis_health()
    overall = "ok" if db_status == "ok" and redis_status == "ok" else "degraded"

    return {
        "status": overall,
        "version": settings.APP_VERSION,
        "db": db_status,
        "redis": redis_status,
    }


def _check_db_health() -> str:
    """Check database connectivity by executing a simple query."""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return "ok"
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return "error"


def _check_redis_health() -> str:
    """Check Redis connectivity by sending a PING command."""
    try:
        client = redis.Redis.from_url(settings.REDIS_URL)
        client.ping()
        client.close()
        return "ok"
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return "error"
