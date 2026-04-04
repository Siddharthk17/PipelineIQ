"""Celery application configuration for PipelineIQ.

Creates and configures the Celery app with Redis as both broker
and result backend. Task modules are auto-discovered from the
tasks package.
"""

import ssl

import sentry_sdk
from celery import Celery
from sentry_sdk.integrations.celery import CeleryIntegration

from backend.config import settings

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[CeleryIntegration()],
        traces_sample_rate=0.1,
        environment=settings.ENVIRONMENT,
        release=f"pipelineiq@{settings.APP_VERSION}",
        send_default_pii=False,
    )

celery_app = Celery(
    "pipelineiq",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.config_from_object("backend.celery_config")

# Upstash Redis requires TLS — configure SSL for both broker and backend
if settings.CELERY_BROKER_URL.startswith("rediss://"):
    celery_app.conf.broker_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}
    celery_app.conf.redis_backend_use_ssl = {"ssl_cert_reqs": ssl.CERT_NONE}

celery_app.autodiscover_tasks(["backend.tasks"], related_name="pipeline_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="webhook_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="schedule_tasks")
celery_app.autodiscover_tasks(["backend.tasks"], related_name="notification_tasks")
