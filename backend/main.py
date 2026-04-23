"""FastAPI application factory for PipelineIQ.

Configures the application with lifespan management, exception handlers,
middleware, route mounting, and health checks. This is the single
entry point for the application.
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import sentry_sdk
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse, JSONResponse, ORJSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.router import api_router, legacy_api_router
from backend.api.auth import router as auth_router
from backend.api.webhooks import router as webhooks_router
from backend.api.audit import router as audit_router
from backend.config import settings
from backend.database import create_all_tables, write_engine, read_engine
from backend.db.redis_pools import get_cache_redis
from backend.pipeline.exceptions import PipelineIQError
from backend.services.storage_service import storage_service
from backend.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
        ],
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
        release=f"pipelineiq@{settings.APP_VERSION}",
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup and shutdown."""
    logger.info(
        "Starting %s v%s (debug=%s, log_level=%s)",
        settings.APP_NAME,
        settings.APP_VERSION,
        settings.DEBUG,
        settings.LOG_LEVEL,
    )
    if settings.ENVIRONMENT == "development":
        create_all_tables()
    _validate_upload_dir()
    _init_storage_bucket()
    logger.info("Application startup complete")

    yield

    logger.info("Shutting down %s", settings.APP_NAME)
    if read_engine is write_engine:
        write_engine.dispose()
    else:
        read_engine.dispose()
        write_engine.dispose()
    logger.info("Application shutdown complete")


def _validate_upload_dir() -> None:
    """Ensure the upload directory exists and is writable."""
    upload_dir = settings.UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    # Unique probe filename avoids worker races under multi-process startup.
    test_file = upload_dir / f".write_test_{uuid.uuid4().hex}"
    try:
        test_file.touch()
        test_file.unlink()
        logger.info("Upload directory validated: %s", upload_dir)
    except OSError as exc:
        logger.error("Upload directory '%s' is not writable: %s", upload_dir, exc)
        raise


def _init_storage_bucket() -> None:
    """Initialize S3/MinIO bucket if using S3 storage."""
    if settings.STORAGE_TYPE != "s3":
        return
    try:
        import boto3
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
        )
        bucket_name = settings.S3_BUCKET
        try:
            s3_client.create_bucket(Bucket=bucket_name)
            logger.info("Created S3 bucket: %s", bucket_name)
        except s3_client.exceptions.BucketAlreadyOwnedByYou:
            logger.info("S3 bucket already exists: %s", bucket_name)
        except s3_client.exceptions.BucketAlreadyExists:
            logger.warning("S3 bucket name already taken by another account: %s", bucket_name)
    except Exception as exc:
        logger.warning("Failed to initialize S3 bucket: %s", exc)


app = FastAPI(
    title="PipelineIQ API",
    description="""
    Data pipeline orchestration with column-level lineage tracking.

    ## Features
    - YAML pipeline definition with 9 step types
    - Column-level lineage tracking and impact analysis
    - Schema drift detection between pipeline runs
    - Pipeline versioning with diff view
    - Dry-run execution planning
    - Real-time execution via Server-Sent Events
    - Data quality validation rules engine
    """,
    version="3.6.2",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    contact={
        "name": "PipelineIQ Team",
        "url": "https://github.com/pipelineiq",
    },
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
)

if settings.ENVIRONMENT != "production":

    @app.get("/redoc", include_in_schema=False)
    async def redoc_html() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title=f"{app.title} - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js",
        )


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Prometheus metrics — imported from centralized module to avoid circular imports
from backend.metrics import (
    PIPELINE_RUNS_TOTAL,
    PIPELINE_DURATION_SECONDS,
    FILES_UPLOADED_TOTAL,
    ACTIVE_USERS,
    CELERY_QUEUE_DEPTH,
)

Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    excluded_handlers=["/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.exception_handler(PipelineIQError)
async def pipelineiq_error_handler(
    request: Request, exc: PipelineIQError
) -> JSONResponse:
    """Handle PipelineIQ domain errors with structured error bodies."""
    request_id = getattr(request.state, "request_id", "unknown")
    logger.warning("Domain error (request_id=%s): %s", request_id, exc.message)
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
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected errors with a safe error response.

    Never exposes internal details to the client. The request_id
    can be used to find the full traceback in the server logs.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "Unhandled error (request_id=%s): %s",
        request_id,
        exc,
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_type": "InternalServerError",
            "message": "An unexpected error occurred. Check server logs.",
            "request_id": request_id,
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def api_version_middleware(request: Request, call_next):
    """Add API-Version header to all responses for version awareness."""
    response = await call_next(request)
    response.headers["X-API-Version"] = "v1"
    response.headers["X-App-Version"] = settings.APP_VERSION
    return response


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


app.include_router(api_router, prefix=settings.API_PREFIX)
app.include_router(legacy_api_router, prefix="/api")
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(webhooks_router)
app.include_router(audit_router)

from backend.routers.ai import router as ai_router
app.include_router(ai_router)

if settings.ENVIRONMENT != "production":
    from backend.api.debug import router as debug_router

    app.include_router(debug_router)


@app.get(
    "/health",
    response_model=None,
    summary="Health check",
    description="Checks actual DB and Redis connectivity.",
)
def health_check() -> dict:
    """Health check endpoint that verifies DB and Redis connectivity."""
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
        with write_engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return "ok"
    except Exception as exc:
        logger.error("Database health check failed: %s", exc)
        return "error"


def _check_redis_health() -> str:
    """Check Redis connectivity by sending a PING command."""
    try:
        client = get_cache_redis()
        client.ping()
        return "ok"
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        return "error"
