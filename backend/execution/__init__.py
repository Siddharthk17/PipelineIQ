"""Execution backends and routing for large-data pipeline steps.

Keep imports lazy to avoid importing optional heavy dependencies (pyarrow/duckdb)
when only lightweight modules (e.g. sql_builder) are needed.
"""

__all__ = [
    "ArrowDataBus",
    "DuckDBExecutor",
    "SmartExecutor",
    "close_worker_duckdb",
    "get_arrow_bus",
    "get_worker_duckdb",
    "initialize_worker_duckdb",
]


def __getattr__(name: str):
    if name in {"ArrowDataBus", "get_arrow_bus"}:
        from backend.execution.arrow_bus import ArrowDataBus, get_arrow_bus

        return {"ArrowDataBus": ArrowDataBus, "get_arrow_bus": get_arrow_bus}[name]
    if name in {
        "DuckDBExecutor",
        "close_worker_duckdb",
        "get_worker_duckdb",
        "initialize_worker_duckdb",
    }:
        from backend.execution.duckdb_executor import (
            DuckDBExecutor,
            close_worker_duckdb,
            get_worker_duckdb,
            initialize_worker_duckdb,
        )

        return {
            "DuckDBExecutor": DuckDBExecutor,
            "close_worker_duckdb": close_worker_duckdb,
            "get_worker_duckdb": get_worker_duckdb,
            "initialize_worker_duckdb": initialize_worker_duckdb,
        }[name]
    if name == "SmartExecutor":
        from backend.execution.smart_executor import SmartExecutor

        return SmartExecutor
    raise AttributeError(f"module 'backend.execution' has no attribute '{name}'")
