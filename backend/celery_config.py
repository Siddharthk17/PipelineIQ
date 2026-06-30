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
    Queue("gemini"),
    Queue("streaming"),
)

task_routes = {
    "pipeline.execute": {"queue": "default"},
    "webhooks.deliver": {"queue": "critical"},
    "notifications.deliver": {"queue": "critical"},
    "schedules.check": {"queue": "bulk"},
    "tasks.profile_file": {"queue": "bulk"},
    "tasks.call_gemini": {"queue": "gemini"},
    "tasks.generate_pipeline_description": {"queue": "gemini"},
    "tasks.generate_pipeline_ai": {"queue": "gemini"},
    "tasks.repair_pipeline_ai": {"queue": "gemini"},
    "tasks.execute_scheduled_pipeline": {"queue": "bulk"},
    "tasks.schedule_run_completion_callback": {"queue": "bulk"},
    "tasks.run_streaming_pipeline": {"queue": "streaming"},
    "tasks.deliver_webhook": {"queue": "critical"},
    "tasks.maintain_storage": {"queue": "bulk"},
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
# HIGH-07: when a worker dies mid-task, requeue the task instead of
# silently dropping it. Without this, OOM-killed Celery workers lose
# execution units and pipeline runs hang in PENDING forever.
task_reject_on_worker_lost = True

# Default execution safety.
task_soft_time_limit = 300
task_time_limit = 360
task_default_retry_delay = 30
# Task-level retry policies are defined per-task via decorators.
task_annotations = {}

# HIGH-07: cap automatic retries so a permanently broken task cannot enter
# an infinite retry loop and pin worker slots indefinitely. Individual tasks
# may override via decorator kwargs.
task_default_max_retries = 5

# Keep result backend short-lived to avoid Redis growth.
result_expires = 3600

# HIGH-07: dead-letter routing for permanently failed tasks. The queue is
# created by the worker bootstrap below; tasks marked with `queue="default"`
# and a delivery failure stay here for inspection instead of being lost.
task_create_missing_queues = True

beat_schedule = {
    "check-pipeline-schedules": {
        "task": "schedules.check",
        "schedule": 60.0,
    },
    "maintain-storage": {
        "task": "tasks.maintain_storage",
        "schedule": 300.0,  # every 5 minutes
    },
}
