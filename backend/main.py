"""FastAPI application factory for PipelineIQ.

Configures the application with lifespan management, exception handlers,
middleware, route mounting, and health checks. This is the single
entry point for the application.
"""

import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import orjson
import sentry_sdk
import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse, JSONResponse, ORJSONResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from redis.exceptions import RedisError
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from slowapi import _rate_limit_exceeded_handler as _slowapi_rate_limit_handler
from slowapi.errors import RateLimitExceeded

from backend.api.audit import router as audit_router
from backend.api.auth import router as auth_router
from backend.api.router import api_router, legacy_api_router
from backend.api.webhooks import router as webhooks_router
from backend.config import settings
from backend.database import create_all_tables, read_engine, write_engine
from backend.db.redis_pools import (
    get_broker_redis,
    get_cache_redis,
    get_pubsub_redis,
    get_yjs_redis,
)
from backend.pipeline.exceptions import PipelineIQError
from backend.routers.ai import router as ai_router
from backend.routers.streaming import router as streaming_router
from backend.routers.wasm import router as wasm_router
from backend.services.storage_service import S3StorageProvider, storage_service
from backend.telemetry import instrument_all
from backend.utils.rate_limiter import limiter

logger = logging.getLogger(__name__)

# OTel is initialized inside instrument_all() during lifespan startup.
# Module-level setup_telemetry() is intentionally deferred to avoid
# double-initialization conflicts with the pre-fork model.

# Import the shared `ClientDisconnected` from the exceptions module to
# avoid a circular import between `main.py` and the routers that
# raise it. (Routers are imported at module load time below.)
from backend.pipeline.exceptions import ClientDisconnected, PipelineYAMLTooLargeError


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
    except OSError:
        logger.exception(
            "Upload directory '%s' is not writable",
            upload_dir)
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
        _ensure_minio_buckets()
        _apply_minio_lifecycle_policies()
        _STARTUP_MARKER_PATH.write_text("initialized\n", encoding="utf-8")


def _init_storage_bucket() -> None:
    """Initialize S3/MinIO bucket if using S3 storage."""
    if settings.STORAGE_TYPE != "s3":
        return
    try:
        import boto3
        from botocore.config import Config
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            endpoint_url=settings.S3_ENDPOINT_URL,
            config=Config(signature_version="s3v4"),
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


def _ensure_minio_buckets() -> None:
    """Create missing MinIO buckets from the fixed bucket list (idempotent)."""
    try:
        from backend.storage.lifecycle import ensure_buckets
        results = ensure_buckets()
        for bucket, status in results.items():
            if status == "created":
                logger.info("MinIO bucket created: %s", bucket)
            elif status != "exists":
                logger.warning("MinIO bucket ensure failed: %s — %s", bucket, status)
    except Exception as exc:
        logger.warning("MinIO bucket setup failed (non-fatal): %s", exc)


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
            openapi_url=app.openapi_url or "/openapi.json",
            title=f"{app.title} - ReDoc",
            redoc_js_url="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js",
        )


app.state.limiter = limiter


async def _rate_limit_exceeded_handler(
    request: Request, exc: Exception
) -> Response:
    """Translate slowapi's handler to FastAPI's expected handler signature."""
    from typing import cast

    return _slowapi_rate_limit_handler(request, cast(RateLimitExceeded, exc))


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RedisError)
async def redis_unavailable_handler(request: Request, exc: RedisError) -> JSONResponse:
    """Cache/queue outages are 503s, not 500s.

    The rate limiter, revocation checks, and SSE cache all read Redis on
    the request path. When Redis is unreachable the honest answer is
    "temporarily unavailable, retry shortly" — and reads that never touch
    Redis (e.g. /health) keep working.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    logger = structlog.get_logger()
    logger.warning(
        "redis_unavailable",
        path=request.url.path,
        error=str(exc)[:200],
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error_type": "ServiceUnavailable",
            "message": "A backing service is temporarily unavailable. Retry shortly.",
            "request_id": request_id,
        },
    )


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

# Prometheus metrics — exposed only outside production, under the same gate
# as /docs, /redoc and /openapi.json above (M-158). Local Prometheus
# scrapes api:8000/metrics in dev/compose; in production the route stays
# unregistered so unauthenticated cardinality data is never served.
if settings.ENVIRONMENT != "production":
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.exception_handler(PipelineYAMLTooLargeError)
async def pipeline_too_large_handler(
    request: Request, exc: PipelineYAMLTooLargeError
) -> JSONResponse:
    """Map oversized pipeline YAML to 413 (not the generic 422)."""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        content={
            "error_type": exc.__class__.__name__,
            "message": exc.message,
            "request_id": request_id,
        },
    )


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


# Maximum logged value sizes for the 422 handler (M-055): values are
# truncated, keys are kept so field-level debugging still works.
_MAX_LOG_VALUE_CHARS = 200
_MAX_LOG_LIST_ITEMS = 20
_MAX_LOG_DEPTH = 6
_REDACTED = "[REDACTED]"

_SENSITIVE_KEY_PARTS = frozenset({
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "client_secret",
    "authorization",
    "cookie",
    "set-cookie",
    "credential",
    "private_key",
    "privatekey",
    "session",
    "pin",
    "otp",
    "passcode",
})


def _looks_sensitive(key: object) -> bool:
    """Return True when a field/header name looks secret-bearing."""
    name = str(key).lower().replace("-", "_")
    return any(part in name for part in _SENSITIVE_KEY_PARTS)


def _truncate_log_value(value: object) -> object:
    if isinstance(value, str):
        return value[:_MAX_LOG_VALUE_CHARS]
    return value


def _redact_log_value(key: object, value: object, _depth: int = 0) -> object:
    """Redact secret-bearing values from structures before logging.

    Field names are preserved; only values are replaced or truncated.
    Deny-logs values, never keys.
    """
    if _depth > _MAX_LOG_DEPTH:
        return _REDACTED
    if key is not None and _looks_sensitive(key):
        return _REDACTED
    if isinstance(value, dict):
        return {
            k: _redact_log_value(k, v, _depth + 1)
            for k, v in list(value.items())[: _MAX_LOG_LIST_ITEMS * 5]
        }
    if isinstance(value, (list, tuple)):
        items = list(value)[:_MAX_LOG_LIST_ITEMS]
        redacted = [_redact_log_value(None, v, _depth + 1) for v in items]
        if len(value) > len(items):
            redacted.append(f"...(+{len(value) - len(items)} more)")
        return redacted
    if isinstance(value, str):
        return value[:_MAX_LOG_VALUE_CHARS]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    # Pydantic ctx carries arbitrary objects (e.g. ValueError); stringify
    # so the response/logging stays JSON-serializable.
    return str(value)[:_MAX_LOG_VALUE_CHARS]


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors with field-level details.

    Logs error locations with secrets redacted: values for
    password/secret/token-like keys are never logged (field names are
    kept), string values are truncated, and the body capture stays
    bounded. Use the ``request_id`` to correlate with the client.
    """
    request_id = getattr(request.state, "request_id", "unknown")
    # Sanitize errors: Pydantic V2 may include non-serializable ctx values.
    # ``input`` echoes the submitted value, so redact it whenever the
    # field name looks secret-bearing.
    safe_errors = []
    for err in exc.errors():
        safe_err = {k: v for k, v in err.items() if k != "ctx"}
        if "ctx" in err:
            safe_err["ctx"] = {
                k: _redact_log_value(k, v) for k, v in err["ctx"].items()
            }
        loc = err.get("loc", ())
        if isinstance(loc, (list, tuple)) and any(
            _looks_sensitive(str(part)) for part in loc
        ):
            safe_err["input"] = "[REDACTED]"
        elif "input" in safe_err and isinstance(safe_err["input"], str):
            safe_err["input"] = _truncate_log_value(safe_err["input"])
        safe_errors.append(safe_err)

    # Capture the request body (best-effort; re-emit a fresh stream so
    # the rest of the stack can still read it). Skip binary bodies and
    # bodies larger than 16 KB to keep logs bounded. Secret-bearing
    # values are redacted before logging (M-055).
    body_summary: dict = {}
    try:
        raw = await request.body()
        if raw and len(raw) <= 16 * 1024:
            try:
                parsed_body = orjson.loads(raw)
                body_summary["json"] = _redact_log_value(None, parsed_body)
            except Exception:
                body_summary["text_preview"] = raw[:512].decode(
                    "utf-8", errors="replace"
                )
        elif raw:
            body_summary["truncated_bytes"] = len(raw)
    except Exception as body_err:
        body_summary["unavailable_reason"] = str(body_err)[:200]

    # Path, query, headers (Authorization/cookie/token-like values redacted;
    # the raw URL is never logged because it may carry ?token= credentials).
    location = {
        "path_params": _redact_log_value(None, dict(request.path_params)),
        "query_params": _redact_log_value(None, dict(request.query_params)),
        "body": body_summary,
    }
    headers_sanitized = {
        k: ("[REDACTED]" if _looks_sensitive(k) else _truncate_log_value(v))
        for k, v in request.headers.items()
    }
    sanitized_url = request.url.path

    logger = structlog.get_logger()
    logger.error(
        "validation_error",
        method=request.method,
        url=sanitized_url,
        headers=headers_sanitized,
        location=location,
        errors=safe_errors,
    )
    # Also log to standard logger for Docker log visibility
    import json as _json
    logging.getLogger("pipelineiq").warning(
        "422 Unprocessable Entity on %s %s", request.method, sanitized_url
    )
    logging.getLogger("pipelineiq").error(
        "Missing/invalid fields: %s", _json.dumps(safe_errors, indent=2)[:4000]
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
        exc: Exception) -> Response:
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

# Added last so it runs first: reject requests with an unexpected Host
# header before any routing, auth, or URL building happens (Host header
# attacks would otherwise poison absolute URLs built from request.base_url,
# e.g. the presigned-upload confirm/download endpoints).
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=[
        host.strip() for host in settings.TRUSTED_HOSTS.split(",") if host.strip()
    ],
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add baseline security headers to every API response (M-138).

    The API serves JSON, never HTML: a deny-by-default frame policy plus
    nosniff/referrer/permissions policies. HSTS is emitted only when the
    request arrived over TLS (compose terminates plain HTTP on loopback;
    production TLS terminates at the ingress).
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def api_version_middleware(request: Request, call_next):
    """Add API-Version header to all responses for version awareness."""
    try:
        response = await call_next(request)
        response.headers["X-API-Version"] = "v1"
        response.headers["X-App-Version"] = settings.APP_VERSION
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise


    else:
        return response
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
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise


    else:
        return response
@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    """Add X-Process-Time header with request processing duration."""
    start = time.perf_counter()
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            return Response(status_code=499)
        raise

    else:
        return response
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
from backend.routers.catalog_pipelines import router as catalog_pipelines_router
from backend.routers.column_policies import router as column_policies_router
from backend.routers.lineage_export import router as lineage_export_router
from backend.routers.runs import router as runs_router
from backend.routers.storage import router as storage_router

app.include_router(catalog_router)
app.include_router(lineage_export_router)
app.include_router(column_policies_router)
app.include_router(catalog_pipelines_router)
app.include_router(storage_router)
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
def readiness_check() -> Response:
    """Readiness probe for all critical runtime dependencies.

    Truthful HTTP semantics (M-170): 200 only when every check passes,
    503 while any dependency (DB, Redis, storage) is down so load
    balancers and Kubernetes (readinessProbe) stop routing traffic here.
    """
    body = _readiness_body()
    return JSONResponse(
        status_code=200 if body["status"] == "ok" else 503,
        content=body,
    )


def _readiness_body() -> dict:
    """Shared readiness payload for /readyz (truthful code) and /health."""
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
    """Backward-compatible alias for readiness checks.

    Always 200 with a status field (existing tests, compose healthchecks
    and CI smoke assert 200 + status in ok/degraded). The truthful
    gate is /readyz, which returns 503 while degraded.
    """
    return _readiness_body()


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
    except Exception:
        logger.exception("Database %s health check failed", role)
        return "error"


    else:
        return "ok"
def _check_redis_health(factory, role: str) -> str:
    """Check Redis connectivity by sending a PING command."""
    try:
        client = factory()
        client.ping()
    except Exception as exc:
        logger.warning("Redis %s health check failed: %s", role, exc)
        return "error"


    else:
        return "ok"
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
    except Exception as exc:
        logger.warning("Storage health check failed: %s", exc)
        return "error"

    else:
        return "ok"