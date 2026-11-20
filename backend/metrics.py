"""Prometheus metrics definitions for PipelineIQ.

Centralizes all custom metric definitions to avoid circular imports
when metrics are needed in multiple modules (e.g., files.py, pipeline_tasks.py).

M-176 note: ACTIVE_USERS_TOTAL and CELERY_QUEUE_DEPTH are gauges — they
only move when something calls the setters below. The counters/histograms
are wired at their call sites (files.py, pipeline_tasks.py); wiring these
two setters into a periodic collector (Celery beat task reading user count
and broker queue depth) is a product-code owner action, tracked in REPORT.md.
Dashboard panels and the CeleryQueueDepthHigh alert already reference these
series, so the definitions must stay even before the writer lands.
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


def set_active_users_total(count: float) -> None:
    """Record the current registered-user count (M-176 write side).

    Intended caller: a periodic collector (Celery beat) that counts users
    and broker queue depth. Kept here so the write path is unit-testable
    without importing task modules.
    """
    ACTIVE_USERS.set(count)


def set_celery_queue_depth(depth: float) -> None:
    """Record the current Celery queue depth (M-176 write side)."""
    CELERY_QUEUE_DEPTH.set(depth)
