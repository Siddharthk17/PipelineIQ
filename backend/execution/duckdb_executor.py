"""DuckDB execution utilities for Arrow-backed pipeline steps.

Architecture:
  One DuckDB connection per Celery worker process.
  Created once at worker startup via Celery's worker_process_init signal.
  Reused across all pipeline tasks that worker handles.

Thread count tuning:
  DuckDB uses internal threads for parallel query execution.
  On a 4-core machine with 4 Celery workers:
  - If each DuckDB uses 4 threads: 4 x 4 = 16 threads competing for 4 cores
  - This causes excessive context switching -- slower than single-threaded
  - Correct: 4 cores / 4 workers = 1 DuckDB thread per worker
  - The parallelism comes from multiple workers, not from within each worker

httpfs extension:
  Configured at worker startup so DuckDB can query Parquet files spilled
  to MinIO directly via S3-compatible API -- no full file download needed.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Callable, Optional

import duckdb
import pyarrow as pa
from celery.signals import worker_process_init as worker_init
from celery.signals import worker_process_shutdown as worker_shutdown

from backend.config import settings
from backend.execution.sql_builder import build_sql_for_step

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_conn: Optional[duckdb.DuckDBPyConnection] = None
_worker_connection = _worker_conn


def _compute_duckdb_thread_count(cpu_cores: int, celery_concurrency: int) -> int:
    return max(1, cpu_cores // celery_concurrency)


def _configure_connection(
    conn: duckdb.DuckDBPyConnection,
    *,
    threads: int = 4,
) -> None:
    conn.sql(f"PRAGMA threads={max(1, int(threads))}")
    conn.sql(f"PRAGMA memory_limit='{settings.WORKER_MEMORY_LIMIT_GB}GB'")

    os.makedirs("/tmp/duckdb", exist_ok=True)
    conn.sql("PRAGMA temp_directory='/tmp/duckdb'")

    conn.sql("PRAGMA enable_object_cache=true")

    if os.environ.get("DUCKDB_ENABLE_HTTPFS", "").lower() != "true":
        logger.info("DuckDB httpfs extension disabled by default")
        return

    try:
        minio_endpoint = os.environ.get("MINIO_ENDPOINT", "minio:9000")
        minio_access_key = os.environ.get("MINIO_ROOT_USER", "minio")
        minio_secret_key = os.environ.get("MINIO_ROOT_PASSWORD", "minio123")
        minio_region = os.environ.get("MINIO_REGION", "us-east-1")

        if not minio_access_key or not minio_secret_key:
            raise ValueError("S3 credentials not configured — set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD")

        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute(f"SET s3_endpoint='{minio_endpoint}'")
        conn.execute("SET s3_use_ssl=false")
        conn.execute("SET s3_url_style='path'")
        conn.execute(f"SET s3_access_key_id='{minio_access_key}'")
        conn.execute(f"SET s3_secret_access_key='{minio_secret_key}'")
        conn.execute(f"SET s3_region='{minio_region}'")
        conn.execute(f"SET s3_allowed_structural_columns=0")
        logger.info("DuckDB httpfs extension loaded for MinIO access (tenant-scoped)")
    except Exception as exc:
        logger.warning(
            "DuckDB httpfs setup failed (MinIO queries will use download fallback): %s",
            exc,
        )


def initialize_worker_duckdb(*, threads: int | None = None) -> duckdb.DuckDBPyConnection:
    """Initialize one DuckDB connection per worker process.

    Thread count is computed dynamically as cpu_cores // celery_concurrency
    to prevent CPU oversaturation when multiple workers share cores.
    The default (threads=None) triggers the dynamic calculation.
    """
    global _worker_conn, _worker_connection
    with _worker_lock:
        if _worker_conn is None:
            if threads is None:
                cpu_cores = os.cpu_count() or 4
                celery_concurrency = int(
                    os.environ.get("CELERY_CONCURRENCY", "4")
                )
                threads = _compute_duckdb_thread_count(
                    cpu_cores, celery_concurrency
                )

            logger.info(
                "Initializing DuckDB for worker: "
                "cpu_cores=%s, celery_concurrency=%s, duckdb_threads=%s",
                os.cpu_count() or 4,
                os.environ.get("CELERY_CONCURRENCY", "4"),
                threads,
            )

            conn = duckdb.connect(database=":memory:")
            _configure_connection(conn, threads=threads)
            _worker_conn = conn
            _worker_connection = conn
            logger.info("Initialized worker DuckDB connection (threads=%d)", threads)
    return _worker_conn


def get_worker_duckdb() -> duckdb.DuckDBPyConnection:
    """Return the worker DuckDB connection, or raise if not initialized."""
    if _worker_conn is None:
        raise RuntimeError(
            "DuckDB worker connection is not initialized. "
            "Call initialize_worker_duckdb() in worker startup hooks."
        )
    return _worker_conn


def close_worker_duckdb() -> None:
    """Close and clear the worker DuckDB connection."""
    global _worker_conn, _worker_connection
    with _worker_lock:
        if _worker_conn is not None:
            try:
                _worker_conn.close()
            finally:
                _worker_conn = None
                _worker_connection = None
            logger.info("Closed worker DuckDB connection")


@worker_init.connect
def _initialize_worker_duckdb_signal(**kwargs) -> None:
    """Create the DuckDB worker connection during Celery process startup."""
    initialize_worker_duckdb()


@worker_shutdown.connect
def _close_worker_duckdb_signal(**kwargs) -> None:
    """Close the DuckDB worker connection and clean up stale shm files during
    Celery worker process shutdown."""
    close_worker_duckdb()

    try:
        from backend.execution.arrow_bus import ArrowDataBus
        cleaned = ArrowDataBus.cleanup_all_stale(max_age_seconds=3600)
        if cleaned > 0:
            logger.info(
                "Cleaned %d stale /dev/shm files during worker shutdown", cleaned
            )
    except Exception as exc:
        logger.warning("Failed to clean stale shm files during shutdown: %s", exc)


class DuckDBExecutor:
    """Executes step SQL against in-memory Arrow relations in DuckDB."""

    def __init__(
        self,
        *,
        connection_getter: Callable[[],
                                    duckdb.DuckDBPyConnection] = get_worker_duckdb,
        local_fallback: bool = True,
        local_threads: int = 2,
    ) -> None:
        self._connection_getter = connection_getter
        self._local_fallback = local_fallback
        self._local_threads = local_threads
        self._local_connection: Optional[duckdb.DuckDBPyConnection] = None
        self._local_lock = threading.Lock()

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        try:
            return self._connection_getter()
        except RuntimeError:
            if not self._local_fallback:
                raise
            with self._local_lock:
                if self._local_connection is None:
                    conn = duckdb.connect(database=":memory:")
                    _configure_connection(conn, threads=self._local_threads)
                    self._local_connection = conn
                    logger.info("Initialized local DuckDB fallback connection")
            return self._local_connection

    def execute_step(
        self,
        step: object,
        input_table: pa.Table,
        *,
        extra_tables: Optional[dict[str, pa.Table]] = None,
    ) -> pa.Table:
        """Execute a single step using DuckDB SQL.

        This is the primary entry point for smart routing. It leverages
        the sql_builder to convert a StepConfig into a DuckDB SQL query.
        """
        sql = build_sql_for_step(step)
        return self.execute_sql(sql, input_table, extra_tables=extra_tables)

    def execute_sql(
        self,
        sql: str,
        input_table: pa.Table,
        *,
        extra_tables: Optional[dict[str, pa.Table]] = None,
    ) -> pa.Table:
        from backend.config import settings as _settings

        conn = self._get_connection()
        table_names = ["__input__"]
        conn.register("__input__", input_table)

        safe_names = {"__input__"}
        if extra_tables:
            for name, table in extra_tables.items():
                safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
                if safe_name in safe_names:
                    safe_name = f"{safe_name}_{len(safe_names)}"
                safe_names.add(safe_name)
                conn.register(safe_name, table)
                table_names.append(safe_name)
                sql = sql.replace(f"{{{name}}}", safe_name)

        old_max_rows = None
        try:
            try:
                old_max_rows = conn.execute("SELECT current_setting('max_rows_to_scan')").fetchone()[0]
            except Exception:
                pass
            conn.execute(f"SET max_rows_to_scan={_settings.WORKER_MAX_ROWS_TO_SCAN}")

            arrow_result = conn.execute(sql).arrow()
            if isinstance(arrow_result, pa.Table):
                return arrow_result
            return arrow_result.read_all()
        finally:
            if old_max_rows is not None:
                try:
                    conn.execute(f"SET max_rows_to_scan={old_max_rows}")
                except Exception:
                    pass
            for name in table_names:
                try:
                    conn.unregister(name)
                except Exception:
                    logger.debug(
                        "DuckDB relation '%s' could not be unregistered", name)

    def close(self) -> None:
        """Close local fallback connection if it exists."""
        with self._local_lock:
            if self._local_connection is not None:
                self._local_connection.close()
                self._local_connection = None
