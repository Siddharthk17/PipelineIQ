"""Prometheus metrics definitions for PipelineIQ.

Centralizes all custom metric definitions to avoid circular imports
when metrics are needed in multiple modules (e.g., files.py, pipeline_tasks.py).
"""

from prometheus_client import Counter, Gauge, Histogram

PIPELINE_RUNS_TOTAL = Counter(
    "pipelineiq_pipeline_runs_total",
    "Total number of pipeline runs",
    ["status"],
)
PIPELINE_DURATION_SECONDS = Histogram(
    "pipelineiq_pipeline_duration_seconds",
    "Pipeline execution duration in seconds",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0],
)
FILES_UPLOADED_TOTAL = Counter(
    "pipelineiq_files_uploaded_total",
    "Total files uploaded",
)
ACTIVE_USERS = Gauge(
    "pipelineiq_active_users_total",
    "Total registered users",
)
CELERY_QUEUE_DEPTH = Gauge(
    "pipelineiq_celery_queue_depth",
    "Current Celery task queue depth",
)
