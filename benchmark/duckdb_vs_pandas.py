"""Benchmark: DuckDB vs Pandas on common pipeline operations.

Runs on your actual hardware with your actual data sizes.
Results are saved to benchmark/results.json and referenced in the README.

Usage:
    python benchmark/duckdb_vs_pandas.py
"""

from __future__ import annotations

import json
import os
import random
import time

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc

random.seed(42)


def generate_orders(n_rows: int) -> pd.DataFrame:
    customers = [f"customer_{i:06d}" for i in range(min(n_rows // 10, 10_000))]
    regions = ["North", "South", "East", "West", "Central"]
    products = [f"product_{i:04d}" for i in range(100)]

    return pd.DataFrame(
        {
            "order_id": range(n_rows),
            "customer_id": [random.choice(customers) for _ in range(n_rows)],
            "product_id": [random.choice(products) for _ in range(n_rows)],
            "region": [random.choice(regions) for _ in range(n_rows)],
            "amount": [
                round(random.uniform(1.0, 1000.0), 2) for _ in range(n_rows)
            ],
            "quantity": [random.randint(1, 10) for _ in range(n_rows)],
            "is_returned": [random.choice([True, False]) for _ in range(n_rows)],
        }
    )


def time_ms(fn, *args, **kwargs) -> float:
    t0 = time.perf_counter()
    fn(*args, **kwargs)
    return (time.perf_counter() - t0) * 1000


def benchmark_aggregation(
    df: pd.DataFrame,
    arrow: pa.Table,
    conn: duckdb.DuckDBPyConnection,
) -> dict:
    pandas_ms = time_ms(
        lambda: df.groupby("customer_id")["amount"].agg(
            ["sum", "count", "mean", "min", "max"]
        )
    )

    conn.register("bench_data", arrow)
    duckdb_ms = time_ms(
        lambda: conn.execute(
            "SELECT customer_id, SUM(amount), COUNT(*), AVG(amount), "
            "MIN(amount), MAX(amount) FROM bench_data GROUP BY customer_id"
        ).df()
    )
    conn.unregister("bench_data")

    return {
        "operation": "GROUP BY aggregation (5 functions)",
        "pandas_ms": round(pandas_ms, 1),
        "duckdb_ms": round(duckdb_ms, 1),
        "speedup": round(pandas_ms / duckdb_ms, 1) if duckdb_ms > 0 else 0,
    }


def benchmark_filter(
    df: pd.DataFrame,
    arrow: pa.Table,
    conn: duckdb.DuckDBPyConnection,
) -> dict:
    pandas_ms = time_ms(lambda: df[df["amount"] > 500.0])

    conn.register("bench_data", arrow)
    duckdb_ms = time_ms(
        lambda: conn.execute(
            "SELECT * FROM bench_data WHERE amount > 500.0"
        ).arrow()
    )
    conn.unregister("bench_data")

    return {
        "operation": "WHERE filter (amount > 500)",
        "pandas_ms": round(pandas_ms, 1),
        "duckdb_ms": round(duckdb_ms, 1),
        "speedup": round(pandas_ms / duckdb_ms, 1) if duckdb_ms > 0 else 0,
    }


def benchmark_join(
    df: pd.DataFrame,
    arrow: pa.Table,
    conn: duckdb.DuckDBPyConnection,
) -> dict:
    n_customers = min(len(df) // 10, 10_000)
    right_df = pd.DataFrame(
        {
            "customer_id": [f"customer_{i:06d}" for i in range(n_customers)],
            "tier": [
                random.choice(["gold", "silver", "bronze"])
                for _ in range(n_customers)
            ],
        }
    )
    right_arrow = pa.Table.from_pandas(right_df)

    pandas_ms = time_ms(lambda: df.merge(right_df, on="customer_id", how="inner"))

    conn.register("bench_left", arrow)
    conn.register("bench_right", right_arrow)
    duckdb_ms = time_ms(
        lambda: conn.execute(
            "SELECT l.*, r.tier FROM bench_left l "
            "INNER JOIN bench_right r ON l.customer_id = r.customer_id"
        ).arrow()
    )
    conn.unregister("bench_left")
    conn.unregister("bench_right")

    return {
        "operation": "INNER JOIN (orders x customers)",
        "pandas_ms": round(pandas_ms, 1),
        "duckdb_ms": round(duckdb_ms, 1),
        "speedup": round(pandas_ms / duckdb_ms, 1) if duckdb_ms > 0 else 0,
    }


def benchmark_arrow_serialization(df: pd.DataFrame) -> dict:
    arrow = pa.Table.from_pandas(df)

    def serialize():
        sink = pa.BufferOutputStream()
        with ipc.new_stream(sink, arrow.schema) as writer:
            writer.write_table(arrow)
        return sink.getvalue().to_pybytes()

    serialize_ms = time_ms(serialize)
    ipc_bytes = serialize()
    deserialize_ms = time_ms(lambda: ipc.open_stream(pa.py_buffer(ipc_bytes)).read_all())

    return {
        "operation": "Arrow IPC serialize + deserialize",
        "serialize_ms": round(serialize_ms, 1),
        "deserialize_ms": round(deserialize_ms, 1),
        "total_ms": round(serialize_ms + deserialize_ms, 1),
        "size_mb": round(len(ipc_bytes) / 1024 / 1024, 1),
    }


def main():
    sizes = [10_000, 100_000, 500_000, 1_000_000]
    all_results = {}

    conn = duckdb.connect(":memory:")
    conn.execute("PRAGMA threads=2")

    print("PipelineIQ Benchmark: DuckDB vs Pandas")
    print("=" * 60)

    for n in sizes:
        print(f"\nGenerating {n:,} row DataFrame...")
        df = generate_orders(n)
        arrow = pa.Table.from_pandas(df)

        results_for_n = {"rows": n, "operations": []}

        print(f"  Running aggregation benchmark ({n:,} rows)...")
        agg = benchmark_aggregation(df, arrow, conn)
        results_for_n["operations"].append(agg)
        print(
            f"    Pandas: {agg['pandas_ms']}ms | "
            f"DuckDB: {agg['duckdb_ms']}ms | "
            f"Speedup: {agg['speedup']}x"
        )

        print(f"  Running filter benchmark ({n:,} rows)...")
        flt = benchmark_filter(df, arrow, conn)
        results_for_n["operations"].append(flt)
        print(
            f"    Pandas: {flt['pandas_ms']}ms | "
            f"DuckDB: {flt['duckdb_ms']}ms | "
            f"Speedup: {flt['speedup']}x"
        )

        print(f"  Running join benchmark ({n:,} rows)...")
        jn = benchmark_join(df, arrow, conn)
        results_for_n["operations"].append(jn)
        print(
            f"    Pandas: {jn['pandas_ms']}ms | "
            f"DuckDB: {jn['duckdb_ms']}ms | "
            f"Speedup: {jn['speedup']}x"
        )

        print(f"  Running Arrow serialization benchmark ({n:,} rows)...")
        ser = benchmark_arrow_serialization(df)
        results_for_n["arrow_serialization"] = ser
        print(
            f"    Serialize: {ser['serialize_ms']}ms | "
            f"Deserialize: {ser['deserialize_ms']}ms | "
            f"Size: {ser['size_mb']}MB"
        )

        all_results[str(n)] = results_for_n

    conn.close()

    os.makedirs("benchmark", exist_ok=True)
    with open("benchmark/results.json", "w") as f:
        json.dump(all_results, f, indent=2)

    print(f"\nResults saved to benchmark/results.json")
    print("\nKey headline numbers:")

    agg_ops = [
        op
        for op in all_results["1000000"]["operations"]
        if "aggregation" in op["operation"]
    ]
    if agg_ops:
        op = agg_ops[0]
        print(
            f"  1M row GROUP BY: Pandas {op['pandas_ms']}ms -> "
            f"DuckDB {op['duckdb_ms']}ms ({op['speedup']}x speedup)"
        )


if __name__ == "__main__":
    main()
