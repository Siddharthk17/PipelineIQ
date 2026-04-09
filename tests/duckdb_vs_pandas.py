#!/usr/bin/env python3
"""Benchmark DuckDB vs Pandas for representative pipeline operations.

Usage:
    python benchmark/duckdb_vs_pandas.py
    python benchmark/duckdb_vs_pandas.py --sizes 10000,50000,100000 --repeats 5
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd
import pyarrow as pa


BENCHMARK_OPS = ("filter", "aggregate", "join", "sort", "sql_projection")


def _build_dataframes(row_count: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    sales = pd.DataFrame(
        {
            "order_id": range(1, row_count + 1),
            "customer_id": [f"C{idx % 20_000:05d}" for idx in range(row_count)],
            "amount": [float((idx * 17) % 1000) for idx in range(row_count)],
            "status": [
                "delivered"
                if idx % 4 == 0
                else "pending"
                if idx % 4 == 1
                else "cancelled"
                if idx % 4 == 2
                else "returned"
                for idx in range(row_count)
            ],
            "region": [("N", "S", "E", "W")[idx % 4] for idx in range(row_count)],
        }
    )
    customers = pd.DataFrame(
        {
            "customer_id": [f"C{idx:05d}" for idx in range(20_000)],
            "segment": [("retail", "enterprise", "smb")[idx % 3] for idx in range(20_000)],
        }
    )
    return sales, customers


def _time_ms(fn: Callable[[], object], repeats: int) -> tuple[float, float, float]:
    # Warmup for cache/jit effects.
    fn()
    samples: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    samples_sorted = sorted(samples)
    p95_idx = max(0, min(len(samples_sorted) - 1, int(len(samples_sorted) * 0.95) - 1))
    return (
        round(statistics.mean(samples), 3),
        round(statistics.median(samples), 3),
        round(samples_sorted[p95_idx], 3),
    )


def _run_pandas(op: str, sales: pd.DataFrame, customers: pd.DataFrame) -> pd.DataFrame:
    if op == "filter":
        return sales[(sales["status"] == "delivered") & (sales["amount"] > 300)].copy()
    if op == "aggregate":
        return (
            sales.groupby("region", as_index=False)
            .agg(amount_sum=("amount", "sum"), order_count=("order_id", "count"))
            .copy()
        )
    if op == "join":
        return sales.merge(customers, on="customer_id", how="left").copy()
    if op == "sort":
        return sales.sort_values(["region", "amount"], ascending=[True, False]).copy()
    if op == "sql_projection":
        filtered = sales[sales["amount"] > 100][["customer_id", "amount"]].copy()
        filtered["amount_x2"] = filtered["amount"] * 2
        return filtered[["customer_id", "amount_x2"]]
    raise ValueError(f"Unknown op '{op}'")


def _run_duckdb(
    op: str,
    conn: duckdb.DuckDBPyConnection,
    sales_arrow: pa.Table,
    customers_arrow: pa.Table,
) -> pa.Table:
    conn.register("__sales__", sales_arrow)
    conn.register("__customers__", customers_arrow)
    try:
        if op == "filter":
            query = """
                SELECT *
                FROM __sales__
                WHERE status = 'delivered' AND amount > 300
            """
        elif op == "aggregate":
            query = """
                SELECT region, SUM(amount) AS amount_sum, COUNT(order_id) AS order_count
                FROM __sales__
                GROUP BY region
            """
        elif op == "join":
            query = """
                SELECT s.*, c.segment
                FROM __sales__ s
                LEFT JOIN __customers__ c USING (customer_id)
            """
        elif op == "sort":
            query = """
                SELECT *
                FROM __sales__
                ORDER BY region ASC, amount DESC
            """
        elif op == "sql_projection":
            query = """
                SELECT customer_id, amount * 2 AS amount_x2
                FROM __sales__
                WHERE amount > 100
            """
        else:
            raise ValueError(f"Unknown op '{op}'")

        result = conn.execute(query).arrow()
        if isinstance(result, pa.Table):
            return result
        return result.read_all()
    finally:
        conn.unregister("__sales__")
        conn.unregister("__customers__")


def _benchmark_size(row_count: int, repeats: int) -> dict:
    sales, customers = _build_dataframes(row_count)
    sales_arrow = pa.Table.from_pandas(sales, preserve_index=False)
    customers_arrow = pa.Table.from_pandas(customers, preserve_index=False)
    conn = duckdb.connect(database=":memory:")
    conn.execute("PRAGMA threads=4")

    result: dict[str, dict[str, float]] = {}
    for op in BENCHMARK_OPS:
        pandas_mean, pandas_median, pandas_p95 = _time_ms(
            lambda op_name=op: _run_pandas(op_name, sales, customers), repeats
        )
        duckdb_mean, duckdb_median, duckdb_p95 = _time_ms(
            lambda op_name=op: _run_duckdb(op_name, conn, sales_arrow, customers_arrow),
            repeats,
        )
        speedup = round(pandas_mean / duckdb_mean, 3) if duckdb_mean > 0 else 0.0
        result[op] = {
            "pandas_ms_mean": pandas_mean,
            "pandas_ms_median": pandas_median,
            "pandas_ms_p95": pandas_p95,
            "duckdb_ms_mean": duckdb_mean,
            "duckdb_ms_median": duckdb_median,
            "duckdb_ms_p95": duckdb_p95,
            "speedup": speedup,
        }

    conn.close()
    return result


def run_benchmarks(sizes: list[int], repeats: int) -> dict:
    per_size = {str(size): _benchmark_size(size, repeats) for size in sizes}
    largest = str(max(sizes))
    large_ops = per_size[largest]
    speeds = [metrics["speedup"] for metrics in large_ops.values()]
    faster_ops = sum(1 for s in speeds if s > 1.0)

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark": "duckdb_vs_pandas",
        "parameters": {"sizes": sizes, "repeats": repeats, "operations": list(BENCHMARK_OPS)},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "duckdb_version": duckdb.__version__,
            "pandas_version": pd.__version__,
            "pyarrow_version": pa.__version__,
        },
        "results": per_size,
        "summary": {
            "largest_size": int(largest),
            "average_speedup_largest": round(statistics.mean(speeds), 3),
            "min_speedup_largest": round(min(speeds), 3),
            "ops_faster_on_duckdb_largest": faster_ops,
            "total_ops": len(BENCHMARK_OPS),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DuckDB vs Pandas benchmark.")
    parser.add_argument(
        "--sizes",
        type=str,
        default="10000,50000,100000",
        help="Comma-separated row counts (default: 10000,50000,100000)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Timing repetitions per operation (default: 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="benchmark/results.json",
        help="Output JSON path (default: benchmark/results.json)",
    )
    args = parser.parse_args()

    sizes = [int(item.strip()) for item in args.sizes.split(",") if item.strip()]
    if not sizes:
        raise ValueError("At least one benchmark size is required")

    payload = run_benchmarks(sizes, args.repeats)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote benchmark results to {output_path}")


if __name__ == "__main__":
    main()

