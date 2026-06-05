"""FastAPI application factory for PipelineIQ.

Configures the application with lifespan management, exception handlers,
middleware, route mounting, and health checks. This is the single
entry point for the application.
"""

from backend.routers.ai import router as ai_router
from backend.routers.wasm import router as wasm_router
from backend.routers.streaming import router as streaming_router
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import sentry_sdk
import structlog
import orjson
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse, JSONResponse, ORJSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.api.router import api_router, legacy_api_router
from backend.api.auth import router as auth_router
from backend.api.webhooks import router as webhooks_router
from backend.api.audit import router as audit_router
from backend.config import settings
from backend.database import create_all_tables, write_engine, read_engine
from backend.db.redis_pools import (
    get_broker_redis,
    get_cache_redis,
    get_pubsub_redis,
    get_yjs_redis,
)
from backend.pipeline.exceptions import PipelineIQError
from backend.services.storage_service import S3StorageProvider, storage_service
from backend.utils.rate_limiter import limiter

from backend.telemetry import instrument_all, instrument_fastapi, setup_telemetry, force_flush

logger = logging.getLogger(__name__)

# OTel is initialized inside instrument_all() during lifespan startup.
# Module-level setup_telemetry() is intentionally deferred to avoid
# double-initialization conflicts with the pre-fork model.

# Import the shared `ClientDisconnected` from the exceptions module to
# avoid a circular import between `main.py` and the routers that
# raise it. (Routers are imported at module load time below.)
from backend.pipeline.exceptions import ClientDisconnected  # noqa: E402


# Silence health check and metrics access log noise
class _HealthCheckFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return (
            "/health" not in msg
            and "/healthz" not in msg
            and "/livez" not in msg
            and "/readyz" not in msg
            and "/metrics" not in msg
        )

logging.getLogger("uvicorn.access").disabled = True
logging.getLogger("gunicorn.access").disabled = True

_STARTUP_LOCK_PATH = Path("/tmp/pipelineiq_api_startup.lock")
_STARTUP_MARKER_PATH = Path("/tmp/pipelineiq_api_startup.done")

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
    if settings.AUTO_CREATE_TABLES:
        create_all_tables()
    from backend.database import ensure_pipeline_status_values
    ensure_pipeline_status_values()
    _run_startup_initialization_once()
    from backend.database import engine as _lifespan_db_engine
    instrument_all(app, db_engine=_lifespan_db_engine)
    logger.info("Application startup complete")

    yield

    logger.info("Shutting down %s", settings.APP_NAME)
    from backend.auth import close_bcrypt_pool
    close_bcrypt_pool()
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
        logger.error(
            "Upload directory '%s' is not writable: %s",
            upload_dir,
            exc)
        raise


def _run_startup_initialization_once() -> None:
    """Run expensive startup initialization once per container lifecycle."""
    if _STARTUP_MARKER_PATH.exists():
        return

    _STARTUP_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STARTUP_LOCK_PATH, "a+", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass

        if _STARTUP_MARKER_PATH.exists():
            return

        _validate_upload_dir()
        _init_storage_bucket()
        _apply_minio_lifecycle_policies()
        _STARTUP_MARKER_PATH.write_text("initialized\n", encoding="utf-8")


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
            logger.warning(
                "S3 bucket name already taken by another account: %s",
                bucket_name)
    except Exception as exc:
        logger.warning("Failed to initialize S3 bucket: %s", exc)


def _apply_minio_lifecycle_policies() -> None:
    """Apply MinIO lifecycle policies (idempotent)."""
    try:
        from backend.storage.lifecycle import apply_all_lifecycle_policies
        results = apply_all_lifecycle_policies()
        for bucket, result in results.items():
            if result.get("status") == "ok":
                logger.info("Lifecycle policy OK: %s — %s", bucket, result.get("policy"))
            else:
                logger.warning("Lifecycle policy failed: %s — %s", bucket, result.get("error"))
    except Exception as exc:
        logger.warning("MinIO lifecycle setup failed (non-fatal): %s", exc)


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
    version=settings.APP_VERSION,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
    contact={
        "name": "PipelineIQ Team",
        "url": "https://github.com/pipelineiq",
    },
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.ENVIRONMENT != "production" else None,
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


@app.exception_handler(ClientDisconnected)
async def client_disconnected_handler(
    request: Request, exc: ClientDisconnected
) -> Response:
    """Convert a `ClientDisconnected` raised inside an endpoint into a 499.

    499 is the conventional nginx status for "client closed request".
    Returning an empty body is intentional — the client is no longer
    listening, so any payload is wasted.
    """
    return Response(status_code=499)

# Prometheus metrics — imported from centralized module to avoid circular
# imports

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
    logger = structlog.get_logger()
    logger.warning(
        "domain_error",
        error_type=exc.__class__.__name__,
        message=exc.message,
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
    """Handle Pydantic validation errors with field-level details.

    Logs the offending request body, query params, path params, and the
    field-level error so 422s are no longer a black box in production.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    # Sanitize errors: Pydantic V2 may include non-serializable ctx values
    safe_errors = []
    for err in exc.errors():
        safe_err = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            safe_err["ctx"] = {k: str(v) for k, v in err["ctx"].items()}
        safe_errors.append(safe_err)

    # Capture the request body (best-effort; re-emit a fresh stream so
    # the rest of the stack can still read it). Skip binary bodies and
    # bodies larger than 16 KB to keep logs bounded.
    body_summary: dict = {}
    try:
        raw = await request.body()
        if raw and len(raw) <= 16 * 1024:
            try:
                body_summary["json"] = orjson.loads(raw)
            except Exception:
                body_summary["text_preview"] = raw[:512].decode(
                    "utf-8", errors="replace"
                )
        elif raw:
            body_summary["truncated_bytes"] = len(raw)
    except Exception as body_err:
        body_summary["unavailable_reason"] = str(body_err)

    # Path, query, headers (sanitized for Authorization)
    location = {
        "path_params": dict(request.path_params),
        "query_params": dict(request.query_params),
        "body": body_summary,
    }
    headers_sanitized = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in {"authorization", "cookie"}
    }

    logger = structlog.get_logger()
    logger.error(
        "validation_error",
        method=request.method,
        url=str(request.url),
        headers=headers_sanitized,
        location=location,
        errors=safe_errors,
    )
    # Also log to standard logger for Docker log visibility
    import json as _json
    logging.getLogger("pipelineiq").warning(
        "❌ 422 UNPROCESSABLE ENTITY on %s %s", request.method, request.url
    )
    logging.getLogger("pipelineiq").error(
        "❌ MISSING/INVALID FIELDS: %s", _json.dumps(safe_errors, indent=2)
    )
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
        request: Request,
        exc: Exception) -> JSONResponse:
    """Handle unexpected errors with a safe error response.

    Never exposes internal details to the client. The request_id
    can be used to find the full traceback in the server logs.
    """
    if isinstance(exc, RuntimeError) and str(exc) == "No response returned.":
        return Response(status_code=499)
    
    request_id = getattr(request.state, "request_id", "unknown")
    logger = structlog.get_logger()
    logger.exception(
        "unhandled_exception",
        url=str(request.url),
        method=request.method,
        error_type=type(exc).__name__,
        error=str(exc),
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)


@app.middleware("http")
async def api_version_middleware(request: Request, call_next):
    """Add API-Version header to all responses for version awareness."""
    try:
        response = await call_next(request)
        response.headers["X-API-Version"] = "v1"
        response.headers["X-App-Version"] = settings.APP_VERSION
        return response
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Use Nginx-provided X-Request-ID or generate a new one.

    Propagates the ID into structlog context so every log line
    emitted during this request carries the correlation ID.
    """
    header_request_id = request.headers.get("x-request-id")
    if header_request_id and re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_\-]{0,127}",
        header_request_id,
    ):
        request_id = header_request_id
    else:
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        request_id=request_id,
        method=request.method,
        path=request.url.path,
    )
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Add X-Process-Time header with request processing duration."""
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
        return response
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise

@app.middleware("http")
async def structlog_access_middleware(request: Request, call_next):
    """Log requests in JSON using structlog, avoiding uvicorn access log."""
    start_time = time.perf_counter()
    try:
        response = await call_next(request)
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            # Client disconnected prematurely (e.g., Docker healthcheck timeout)
            path = request.url.path
            if path not in ["/health", "/livez", "/readyz", "/metrics"]:
                logger_access = structlog.get_logger("access")
                logger_access.info(
                    "client_disconnected",
                    method=request.method,
                    path=path,
                )
            return Response(status_code=499)
        raise

    process_time = time.perf_counter() - start_time
    
    path = request.url.path
    if path not in ["/health", "/livez", "/readyz", "/metrics"] and not path.startswith("/api/v1/pipelines/?page="):
        logger_access = structlog.get_logger("access")
        logger_access.info(
            "request_completed",
            method=request.method,
            path=path,
            status_code=response.status_code,
            process_time=f"{process_time:.3f}s",
            client_host=request.client.host if request.client else None,
        )
    return response


app.include_router(api_router, prefix=settings.API_PREFIX)
app.include_router(legacy_api_router, prefix="/api")
app.include_router(auth_router, prefix="/auth", tags=["Authentication"])
app.include_router(webhooks_router)
app.include_router(audit_router)

app.include_router(ai_router)
app.include_router(wasm_router)
app.include_router(streaming_router)

from backend.routers.catalog import router as catalog_router
from backend.routers.lineage_export import router as lineage_export_router
from backend.routers.column_policies import router as column_policies_router
from backend.routers.catalog_pipelines import router as catalog_pipelines_router

from backend.routers.storage import router as storage_router
from backend.routers.contracts import router as contracts_router
from backend.routers.runs import router as runs_router

app.include_router(catalog_router)
app.include_router(lineage_export_router)
app.include_router(column_policies_router)
app.include_router(catalog_pipelines_router)
app.include_router(storage_router)
app.include_router(contracts_router)
app.include_router(runs_router)

if settings.ENVIRONMENT != "production":
    from backend.api.debug import router as debug_router

    app.include_router(debug_router)


@app.get(
    "/livez",
    include_in_schema=False,
    response_model=None,
)
@app.get(
    "/healthz",
    include_in_schema=False,
    response_model=None,
)
def live_check() -> dict:
    """Lightweight liveness endpoint — no DB or Redis calls."""
    return {"status": "ok", "version": settings.APP_VERSION}


@app.get(
    "/readyz",
    include_in_schema=False,
    response_model=None,
)
def readiness_check() -> dict:
    """Readiness probe for all critical runtime dependencies."""
    checks = _collect_health_checks()
    overall = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    redis_summary = (
        "ok"
        if all(
            checks[name] == "ok"
            for name in (
                "redis_broker",
                "redis_pubsub",
                "redis_cache",
                "redis_yjs",
            )
        )
        else "error"
    )
    db_summary = (
        "ok"
        if checks["db_write"] == "ok" and checks["db_read"] == "ok"
        else "error"
    )
    return {
        "status": overall,
        "version": settings.APP_VERSION,
        "db": db_summary,
        "redis": redis_summary,
        "checks": checks,
    }


@app.get(
    "/health",
    response_model=None,
    summary="Health check",
    description="Checks actual runtime readiness across critical dependencies.",
)
@app.get(
    "/api/health",
    include_in_schema=False,
    response_model=None,
)
def health_check() -> dict:
    """Backward-compatible alias for readiness checks."""
    return readiness_check()


def _collect_health_checks() -> dict[str, str]:
    """Collect readiness checks for runtime-critical dependencies."""
    return {
        "db_write": _check_db_health(write_engine, "write"),
        "db_read": _check_db_health(read_engine, "read"),
        "redis_broker": _check_redis_health(get_broker_redis, "broker"),
        "redis_pubsub": _check_redis_health(get_pubsub_redis, "pubsub"),
        "redis_cache": _check_redis_health(get_cache_redis, "cache"),
        "redis_yjs": _check_redis_health(get_yjs_redis, "yjs"),
        "storage": _check_storage_health(),
    }


def _check_db_health(engine, role: str) -> str:
    """Check database connectivity by executing a simple query."""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return "ok"
    except Exception as exc:
        logger.error("Database %s health check failed: %s", role, exc)
        return "error"


def _check_redis_health(factory, role: str) -> str:
    """Check Redis connectivity by sending a PING command."""
    try:
        client = factory()
        client.ping()
        return "ok"
    except Exception as exc:
        logger.warning("Redis %s health check failed: %s", role, exc)
        return "error"


def _check_storage_health() -> str:
    """Check local or S3-compatible storage readiness."""
    try:
        provider = storage_service.provider
        if isinstance(provider, S3StorageProvider):
            provider.s3.head_bucket(Bucket=provider.bucket)
        else:
            upload_dir = Path(settings.UPLOAD_DIR)
            upload_dir.mkdir(parents=True, exist_ok=True)
            probe_path = upload_dir / f".healthcheck_{os.getpid()}"
            probe_path.touch(exist_ok=True)
            probe_path.unlink(missing_ok=True)
        return "ok"
    except Exception as exc:
        logger.warning("Storage health check failed: %s", exc)
        return "error"
