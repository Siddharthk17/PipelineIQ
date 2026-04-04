"""Celery queue routing and execution defaults for PipelineIQ."""

from kombu import Queue

# Queue responsibilities:
# - critical: user-facing notifications and webhook delivery
# - default: pipeline execution
# - bulk: background scheduling and low-priority workload
task_default_queue = "default"
task_queues = (
    Queue("critical"),
    Queue("default"),
    Queue("bulk"),
)

task_routes = {
    "pipeline.execute": {"queue": "default"},
    "webhooks.deliver": {"queue": "critical"},
    "notifications.deliver": {"queue": "critical"},
    "schedules.check": {"queue": "bulk"},
}

task_serializer = "json"
result_serializer = "json"
accept_content = ["json"]
timezone = "UTC"
enable_utc = True
task_track_started = True
task_acks_late = True
worker_prefetch_multiplier = 1
broker_connection_retry_on_startup = True

# Default execution safety and retries.
task_soft_time_limit = 300
task_time_limit = 360
task_default_retry_delay = 30
task_annotations = {"*": {"max_retries": 3}}

# Keep result backend short-lived to avoid Redis growth.
result_expires = 3600

beat_schedule = {
    "check-pipeline-schedules": {
        "task": "schedules.check",
        "schedule": 60.0,
    }
}
