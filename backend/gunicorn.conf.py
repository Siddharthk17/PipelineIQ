"""Gunicorn configuration for PipelineIQ API.

Uses file-based logging to avoid the reentrant I/O crash that occurs when
Gunicorn's SIGCHLD handler attempts to log worker failures while the main
thread holds the stderr lock (Python's _io.BufferedWriter lock).

See: https://docs.gunicorn.org/en/stable/settings.html
"""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "4"))
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000
max_requests = 5000
max_requests_jitter = 500
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Write logs to files instead of stderr/stdout to prevent reentrant
# I/O crashes when signal handlers fire during stderr write contention.
# The /tmp/gunicorn directory is created at container startup.
log_dir = "/tmp/gunicorn"
os.makedirs(log_dir, exist_ok=True)
errorlog = os.path.join(log_dir, "error.log")
accesslog = os.path.join(log_dir, "access.log")

# Capture worker stdout/stderr to the log files instead of stderr.
# This prevents the SIGCHLD handler from crashing when it tries to
# log worker failures while the main thread holds the stderr lock.
capture_output = True

# Preload the app to reduce worker boot time and share memory
preload_app = True
