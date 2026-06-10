import pandas as pd
import numpy as np
import os

# 1M rows, 3 columns
n_rows = 1_000_000
df = pd.DataFrame({
    'id': np.arange(n_rows),
    'val': np.random.randn(n_rows),
    'cat': np.random.choice(['A', 'B', 'C', 'D'], n_rows)
})
df.to_csv('large_stress.csv', index=False)
print(f"Created large_stress.csv with {n_rows} rows")
