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
max_requests = 10000
max_requests_jitter = 1000
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
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


def _dispose_inherited_pools():
    try:
        from backend.database import write_engine, read_engine

        write_engine.dispose(close=False)
        if read_engine is not write_engine:
            read_engine.dispose(close=False)
    except Exception:
        pass

    try:
        from backend.db.redis_pools import (
            _broker_pool, _pubsub_pool, _pubsub_async_pool,
            _cache_pool, _cache_binary_pool, _cache_async_pool,
            _yjs_pool,
        )
        for pool in (
            _broker_pool, _pubsub_pool, _pubsub_async_pool,
            _cache_pool, _cache_binary_pool, _cache_async_pool,
            _yjs_pool,
        ):
            try:
                pool.disconnect()
            except Exception:
                pass
    except Exception:
        pass


def on_starting(server):
    """Gunicorn master boot callback — runs once before forking workers.

    HIGH-17: with preload_app=True the SQLAlchemy engine and Redis pools
    are constructed at import time; their open sockets would otherwise be
    shared across forked workers. We force-dispose the DB engine pool and
    DISCARD Redis connection pools here so each worker rebuilds them on
    first use with fresh sockets owned only by itself.
    """
    _dispose_inherited_pools()


def pre_fork(server, worker):
    """Dispose pools after preload and immediately before fork."""
    _dispose_inherited_pools()


def post_fork(server, worker):
    """After each worker fork — rebuild per-worker pools lazily on first use.

    The master disposes pools in on_starting; workers do not re-open
    eagerly here (lazy reconnect on first request) to keep cold-start cheap.
    We additionally close any inherited bcrypt pool so each worker spawns
    its own bcrypt executor (HIGH-17 companion fix).
    """
    try:
        from backend.auth import close_bcrypt_pool

        close_bcrypt_pool()
    except Exception:
        pass
    _dispose_inherited_pools()


def when_ready(server):
    """Log readiness once the master process is bound."""
    server.log.info("PipelineIQ gunicorn master ready (pid=%s)", os.getpid())


def worker_exit(server, worker):
    """Best-effort cleanup on worker shutdown."""
    try:
        from backend.auth import close_bcrypt_pool

        close_bcrypt_pool()
    except Exception:
        pass
    try:
        from backend.database import write_engine, read_engine

        write_engine.dispose()
        if read_engine is not write_engine:
            read_engine.dispose()
    except Exception:
        pass
