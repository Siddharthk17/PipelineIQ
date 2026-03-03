"""Celery application configuration for PipelineIQ.

Creates and configures the Celery app with Redis as both broker
and result backend. Task modules are auto-discovered from the
tasks package.
"""

# Third-party packages
from celery import Celery

# Internal modules
from backend.config import settings

celery_app = Celery(
    "pipelineiq",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

# Auto-discover tasks in the tasks package
celery_app.autodiscover_tasks(["backend.tasks"], related_name="pipeline_tasks")
