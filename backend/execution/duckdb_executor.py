"""DuckDB execution utilities for Arrow-backed pipeline steps."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

import duckdb
import pyarrow as pa

from backend.execution.sql_builder import build_sql_for_step

logger = logging.getLogger(__name__)

_worker_lock = threading.Lock()
_worker_connection: Optional[duckdb.DuckDBPyConnection] = None


def _configure_connection(
    conn: duckdb.DuckDBPyConnection,
    *,
    threads: int = 4,
) -> None:
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    conn.execute("PRAGMA temp_directory='/tmp'")
    conn.execute("PRAGMA enable_object_cache=true")


def initialize_worker_duckdb(*, threads: int = 4) -> duckdb.DuckDBPyConnection:
    """Initialize one DuckDB connection per worker process."""
    global _worker_connection
    with _worker_lock:
        if _worker_connection is None:
            conn = duckdb.connect(database=":memory:")
            _configure_connection(conn, threads=threads)
            _worker_connection = conn
            logger.info("Initialized worker DuckDB connection")
    return _worker_connection


def get_worker_duckdb() -> duckdb.DuckDBPyConnection:
    """Return the worker DuckDB connection, or raise if not initialized."""
    if _worker_connection is None:
        raise RuntimeError(
            "DuckDB worker connection is not initialized. "
            "Call initialize_worker_duckdb() in worker startup hooks."
        )
    return _worker_connection


def close_worker_duckdb() -> None:
    """Close and clear the worker DuckDB connection."""
    global _worker_connection
    with _worker_lock:
        if _worker_connection is not None:
            try:
                _worker_connection.close()
            finally:
                _worker_connection = None
            logger.info("Closed worker DuckDB connection")


class DuckDBExecutor:
    """Executes step SQL against in-memory Arrow relations in DuckDB."""

    def __init__(
        self,
        *,
        connection_getter: Callable[[], duckdb.DuckDBPyConnection] = get_worker_duckdb,
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
        """Execute a single step against Arrow input and return Arrow output."""
        sql = build_sql_for_step(step)
        return self.execute_sql(sql, input_table, extra_tables=extra_tables)

    def execute_sql(
        self,
        sql: str,
        input_table: pa.Table,
        *,
        extra_tables: Optional[dict[str, pa.Table]] = None,
    ) -> pa.Table:
        conn = self._get_connection()
        table_names = ["__input__"]
        conn.register("__input__", input_table)

        if extra_tables:
            for name, table in extra_tables.items():
                conn.register(name, table)
                table_names.append(name)

        try:
            arrow_result = conn.execute(sql).arrow()
            if isinstance(arrow_result, pa.Table):
                return arrow_result
            return arrow_result.read_all()
        finally:
            for name in table_names:
                try:
                    conn.unregister(name)
                except Exception:
                    # Best-effort cleanup; unregister failure should not mask step errors.
                    logger.debug("DuckDB relation '%s' could not be unregistered", name)

    def close(self) -> None:
        """Close local fallback connection if it exists."""
        with self._local_lock:
            if self._local_connection is not None:
                self._local_connection.close()
                self._local_connection = None

