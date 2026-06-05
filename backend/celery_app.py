"""Celery application configuration for PipelineIQ.

Creates and configures the Celery app with Redis as both broker
and result backend. Task modules are auto-discovered from the
tasks package.

OTel and Redis instrumentation are initialized ONLY in worker
child processes via `worker_process_init` to avoid fork-safety
issues with gRPC channels and singleton instrumentors.
"""

import ssl

import sentry_sdk
from celery import Celery
from celery.signals import worker_process_init, worker_process_shutdown
from sentry_sdk.integrations.celery import CeleryIntegration

from backend.config import settings
from backend.execution.duckdb_executor import close_worker_duckdb, initialize_worker_duckdb


celery_app = Celery(
    "pipelineiq",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.config_from_object("backend.celery_config")

# Upstash Redis requires TLS — configure SSL for both broker and backend
if settings.CELERY_BROKER_URL.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}
if settings.CELERY_RESULT_BACKEND.startswith("rediss://"):
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_REQUIRED}

celery_app.autodiscover_tasks(["backend.tasks"], related_name="pipeline_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="webhook_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="schedule_tasks")
celery_app.autodiscover_tasks(
    ["backend.tasks"],
    related_name="notification_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="profiling")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="gemini_tasks")
celery_app.autodiscover_tasks(
    ["backend.tasks"],
    related_name="scheduled_pipeline")
celery_app.autodiscover_tasks(
    ["backend.tasks"],
    related_name="streaming_pipeline")
celery_app.autodiscover_tasks(
    ["backend.tasks"],
    related_name="storage_maintenance")

def _init_sentry_for_worker() -> None:
    """Initialize Sentry after Celery forks the worker process."""
    if not settings.SENTRY_DSN:
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[CeleryIntegration()],
        traces_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
        release=f"pipelineiq@{settings.APP_VERSION}",
        send_default_pii=False,
    )


@worker_process_init.connect
def _init_worker_process(**kwargs) -> None:
    """Initialize per-process resources AFTER Celery fork.

    OTel and Redis instrumentation MUST be initialized here, not at
    module level, because:
    1. Celery uses a pre-fork model — module-level code runs in the
       parent process and is inherited (not re-run) by forked workers.
    2. gRPC channels used by the OTLP exporter are not fork-safe.
    3. CeleryInstrumentor/RedisInstrumentor are singletons — calling
       .instrument() in the parent poisons the global state for all
       children, causing "Overriding of current TracerProvider" warnings.
    """
    from backend.telemetry import (
        instrument_redis,
        reset_telemetry,
        setup_celery_telemetry,
        setup_telemetry,
    )

    _init_sentry_for_worker()
    reset_telemetry()
    setup_telemetry()
    setup_celery_telemetry()
    instrument_redis()
    initialize_worker_duckdb()


@worker_process_shutdown.connect
def _close_worker_process(**kwargs) -> None:
    """Flush OTel spans and close DuckDB on worker shutdown."""
    from backend.telemetry import force_flush
    try:
        force_flush()
    except Exception:
        pass
    close_worker_duckdb()
