import os
import pyarrow as pa
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import numpy as np
from backend.execution.arrow_bus import get_arrow_bus
from backend.execution.duckdb_executor import DuckDBExecutor
from backend.config import settings

def create_table(rows, cols, size_mb=None):
    if size_mb:
        # Estimate bytes: rows * cols * 8 (float64)
        rows = int((size_mb * 1024 * 1024) / (cols * 8))
    
    data = {f"col_{i}": np.random.randn(rows) for i in range(cols)}
    return pa.Table.from_pydict(data)

def test_tiered_storage():
    bus = get_arrow_bus()
    run_id = "test_run_123"
    
    # 1. Small table (< 10MB) -> Redis
    print("Testing small table (Redis)...")
    small_table = create_table(1000, 10)
    tier_s = bus.put("small", small_table, run_id=run_id)
    print(f"Small table tier: {tier_s}")
    assert tier_s == "redis"
    
    # 2. Medium table (10MB < x < 500MB) -> SHM
    print("\nTesting medium table (SHM)...")
    medium_table = create_table(0, 10, size_mb=100)
    tier_m = bus.put("medium", medium_table, run_id=run_id)
    print(f"Medium table tier: {tier_m}")
    assert tier_m == "shm"
    
    # 3. Large table (> 500MB) -> Spill
    print("\nTesting large table (Spill)...")
    large_table = create_table(0, 10, size_mb=600)
    tier_l = bus.put("large", large_table, run_id=run_id)
    print(f"Large table tier: {tier_l}")
    assert tier_l == "spill"
    
    # Verify retrieval
    print("\nVerifying retrieval...")
    assert bus.get("small").num_rows == small_table.num_rows
    assert bus.get("medium").num_rows == medium_table.num_rows
    assert bus.get("large").num_rows == large_table.num_rows
    print("All retrievals successful.")
    
    # Verify cleanup
    print("\nVerifying cleanup...")
    bus.cleanup_run(run_id)
    try:
        bus.get("small")
        print("Error: Small table still exists")
    except KeyError:
        print("Small table cleaned up.")
        
    try:
        bus.get("medium")
        print("Error: Medium table still exists")
    except KeyError:
        print("Medium table cleaned up.")
        
    try:
        bus.get("large")
        print("Error: Large table still exists")
    except KeyError:
        print("Large table cleaned up.")

def test_duckdb_execution():
    print("\nTesting DuckDB execution on spilled table...")
    bus = get_arrow_bus()
    run_id = "test_run_duckdb"
    
    # Create a large table and spill it
    table = create_table(0, 5, size_mb=600)
    bus.put("input_table", table, run_id=run_id)
    
    executor = DuckDBExecutor()
    
    # Get the table from the bus to pass it as the input_table
    input_table = bus.get("input_table")
    
    # Test a simple SQL query on the spilled table
    # The input_table is registered as "__input__" inside execute_sql
    query = "SELECT SUM(col_0) FROM __input__"
    result_table = executor.execute_sql(query, input_table)
    
    print(f"Query result: {result_table.to_pydict()}")
    assert result_table.num_rows == 1
    
    bus.cleanup_run(run_id)
    print("DuckDB execution successful and cleaned up.")

if __name__ == "__main__":
    try:
        test_tiered_storage()
        test_duckdb_execution()
        print("\nALL VERIFICATIONS PASSED")
    except Exception as e:
        print(f"\nVERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
