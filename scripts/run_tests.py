import requests
import json
import time

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOTI3ZmQ5ZS0yNGUwLTQ3NDgtOWUyZS04ZDcxZGZlOTE5NmMiLCJyb2xlIjoiYWRtaW4iLCJleHAiOjE3NzYwMDIxOTB9.fPFkPrrw1T9UbzCg8WBdxBHI_cztEpNpvzaA1QjA8pE"
BASE_URL = "http://localhost"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

def run_pipeline(name, yaml_config):
    print(f"Running pipeline: {name}")
    resp = requests.post(f"{BASE_URL}/api/v1/pipelines/run", headers=HEADERS, json={
        "yaml_config": yaml_config,
        "name": name
    })
    if resp.status_code != 202:
        print(f"Error starting {name}: {resp.text}")
        return None
    run_id = resp.json()["run_id"]
    print(f"Started {name}, run_id: {run_id}")
    
    # Poll for completion
    while True:
        status_resp = requests.get(f"{BASE_URL}/api/v1/pipelines/{run_id}", headers=HEADERS)
        status = status_resp.json().get("status")
        print(f"Run {name} status: {status}")
        if status in ["COMPLETED", "FAILED", "CANCELLED"]:
            break
        time.sleep(2)
    return run_id

# Test 1: Pandas Routing
yaml_small = """
pipeline:
  name: pandas_test
  steps:
    - name: load_small
      type: load
      file_id: "d304d177-1fa9-47b5-8bd4-fa5d3bc40cb3"
    - name: save_small
      type: save
      input: load_small
      filename: pandas_output
"""

# Test 2: DuckDB Routing
yaml_large = """
pipeline:
  name: duckdb_test
  steps:
    - name: load_large
      type: load
      file_id: "e0c85eec-fb34-4596-91cf-1c26435bcdc8"
    - name: save_large
      type: save
      input: load_large
      filename: duckdb_output
"""

run_pipeline("pandas_test", yaml_small)
run_pipeline("duckdb_test", yaml_large)
