import time
import uuid
import pandas as pd
import pyarrow as pa
import pytest
import numpy as np

from backend.execution.smart_executor import SmartExecutor
from backend.execution.arrow_bus import ArrowDataBus, get_arrow_bus
from backend.execution.duckdb_executor import DuckDBExecutor
from backend.pipeline.steps import StepExecutor
from backend.pipeline.parser import (
    StepType,
    FilterOperator,
    FilterStepConfig,
    AggregateStepConfig,
    SqlStepConfig,
)


def generate_dataset(rows: int, cols: int = 5):
    """Generate a synthetic pandas DataFrame."""
    data = {
        f"col_{i}": np.random.randn(rows) if i != 0 else np.random.randint(0, 100, rows)
        for i in range(cols)
    }
    # Add a categorical column for aggregation
    data["category"] = np.random.choice(["A", "B", "C", "D"], rows)
    return pd.DataFrame(data)


def run_benchmark_step(executor, bus, run_id, step_name, step_config, input_key=None):
    """Executes a step and measures duration."""
    start = time.perf_counter()

    # Load input table from bus
    input_table = None
    if input_key:
        input_table = bus.get(input_key)

    # Execute step
    # Note: SmartExecutor.execute_step returns StepExecutionResult
    from backend.pipeline.lineage import LineageRecorder

    recorder = LineageRecorder()
    registry = {input_key: input_table} if input_key else {}
    result = executor.execute_step(step_config, registry, recorder)
    result_table = result.output_table

    # Store output
    bus.put(step_name, result_table, run_id=run_id)

    duration = time.perf_counter() - start
    tier = bus.locations[step_name]["tier"]
    size = bus.locations[step_name]["size_bytes"]

    return duration, tier, size


def benchmark_pipeline(rows: int):
    """Runs a sample pipeline on a dataset of size 'rows'."""
    run_id = str(uuid.uuid4())
    bus = get_arrow_bus()
    bus.clear_all()

    # Initialize executors
    pandas_executor = StepExecutor()
    duckdb_executor = DuckDBExecutor()
    executor = SmartExecutor(pandas_executor, duckdb_executor)

    # 1. Load (simulated by creating a table and putting it in bus)
    df = generate_dataset(rows)
    table = pa.Table.from_pandas(df)
    bus.put("load", table, run_id=run_id)

    # 2. Filter
    filter_cfg = FilterStepConfig(
        name="filter_step",
        step_type=StepType.FILTER,
        input="load",
        column="col_0",
        operator=FilterOperator.GREATER_THAN,
        value=50,
    )
    t_filter, tier_filter, s_filter = run_benchmark_step(
        executor, bus, run_id, "filter_step", filter_cfg, "load"
    )

    # 3. Aggregate
    agg_cfg = AggregateStepConfig(
        name="agg_step",
        step_type=StepType.AGGREGATE,
        input="filter_step",
        group_by=["category"],
        aggregations=[{"column": "col_1", "function": "sum"}],
    )
    t_agg, tier_agg, s_agg = run_benchmark_step(
        executor, bus, run_id, "agg_step", agg_cfg, "filter_step"
    )

    # 4. SQL (Direct DuckDB)
    sql_cfg = SqlStepConfig(
        name="sql_step",
        step_type=StepType.SQL,
        input="filter_step",
        query="SELECT category, AVG(col_2) as avg_val FROM {{input}} GROUP BY category",
    )
    t_sql, tier_sql, s_sql = run_benchmark_step(
        executor, bus, run_id, "sql_step", sql_cfg, "filter_step"
    )

    return {
        "rows": rows,
        "filter": (t_filter, tier_filter, s_filter),
        "agg": (t_agg, tier_agg, s_agg),
        "sql": (t_sql, tier_sql, s_sql),
    }


if __name__ == "__main__":
    sizes = [10_000, 100_000, 1_000_000, 5_000_000]

    print("\nPipelineIQ Zero-Copy Benchmarks\n" + "=" * 40)

    for size in sizes:
        print(f"\nBenchmarking {size:,} rows...", end=" ", flush=True)
        res = benchmark_pipeline(size)

        print(f"Done.")
        print(f"{'Step':<12} | {'Time':<10} | {'Tier':<10} | {'Size':<10}")
        print("-" * 45)
        for step in ["filter", "agg", "sql"]:
            t, tier, s = res[step]
            print(f"{step:<12} | {t:.4f}s    | {tier:<10} | {s / (1024 * 1024):.2f} MB")
