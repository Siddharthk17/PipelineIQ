import os
from pathlib import Path
import pyarrow as pa
import numpy as np
from backend.execution.arrow_bus import get_arrow_bus

def create_table(rows, cols):
    data = {f"col_{i}": np.random.randn(rows) for i in range(cols)}
    return pa.Table.from_pydict(data)

def test_shm_cleanup():
    bus = get_arrow_bus()
    run_id = "leak_test_run"
    shm_dir = Path("/dev/shm")
    
    print("Creating multiple medium tables in SHM...")
    for i in range(10):
        table = create_table(100000, 10) # ~8MB per table, should be in Redis or SHM
        # Force it to SHM by making it > 10MB
        table = create_table(1000000, 20) # ~160MB
        bus.put(f"table_{i}", table, run_id=run_id)
    
    print(f"Files in /dev/shm before cleanup: {len(list(shm_dir.glob('piq_*')))}")
    
    bus.cleanup_run(run_id)
    
    remaining = list(shm_dir.glob('piq_*'))
    print(f"Files in /dev/shm after cleanup: {len(remaining)}")
    
    if len(remaining) > 0:
        print(f"Leak detected! Remaining files: {remaining}")
        exit(1)
    else:
        print("No leaks detected in /dev/shm.")

if __name__ == "__main__":
    try:
        test_shm_cleanup()
        print("\nMEMORY LEAK VERIFICATION PASSED")
    except Exception as e:
        print(f"\nMEMORY LEAK VERIFICATION FAILED: {e}")
        exit(1)
