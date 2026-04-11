import pyarrow as pa
import numpy as np
from backend.execution.duckdb_executor import DuckDBExecutor
from backend.execution.arrow_bus import get_arrow_bus

def create_table(rows, cols):
    data = {f"col_{i}": np.random.randn(rows) for i in range(cols)}
    data["category"] = np.random.choice(["A", "B", "C"], rows)
    return pa.Table.from_pydict(data)

def test_complex_sql():
    print("Testing complex SQL (CTEs and Window Functions)...")
    bus = get_arrow_bus()
    run_id = "test_edge_cases"
    
    # Create a table with some data
    table = create_table(1000, 5)
    bus.put("input_table", table, run_id=run_id)
    input_table = bus.get("input_table")
    
    executor = DuckDBExecutor()
    
    # 1. CTE + Window Function
    query = """
    WITH stats AS (
        SELECT 
            category, 
            col_0, 
            AVG(col_0) OVER (PARTITION BY category) as avg_cat,
            RANK() OVER (PARTITION BY category ORDER BY col_0 DESC) as rank_cat
        FROM __input__
    )
    SELECT * FROM stats WHERE rank_cat <= 5
    """
    
    try:
        result = executor.execute_sql(query, input_table)
        print(f"Complex SQL result rows: {result.num_rows}")
        assert result.num_rows <= 15 # 3 categories * 5 ranks
        print("Complex SQL successful.")
    except Exception as e:
        print(f"Complex SQL failed: {e}")
        raise

    # 2. Invalid SQL
    print("\nTesting invalid SQL...")
    invalid_query = "SELECT * FROM non_existent_table"
    try:
        executor.execute_sql(invalid_query, input_table)
        print("Error: Invalid SQL should have failed")
    except Exception as e:
        print(f"Invalid SQL failed as expected: {e}")

    bus.cleanup_run(run_id)

if __name__ == "__main__":
    try:
        test_complex_sql()
        print("\nEDGE CASE VERIFICATIONS PASSED")
    except Exception as e:
        print(f"\nEDGE CASE VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
