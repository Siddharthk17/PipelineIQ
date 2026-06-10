import requests
import json
import time
import os
import pandas as pd
import pyarrow as pa

API_URL = "http://localhost"
USER_EMAIL = "demo@pipelineiq.app"
USER_PASS = "Demo1234!"

def get_token():
    resp = requests.post(f"{API_URL}/auth/login", 
                         json={"email": USER_EMAIL, "password": USER_PASS})
    if resp.status_code != 200:
        print(f"Login failed: {resp.text}")
        return None
    return resp.json()["access_token"]

def upload_file(token, filename, row_count):
    df = pd.DataFrame({
        "id": range(row_count),
        "val": [f"val_{i}" for i in range(row_count)],
        "num": [float(i) for i in range(row_count)]
    })
    df.to_csv(filename, index=False)
    
    headers = {"Authorization": f"Bearer {token}"}
    with open(filename, "rb") as f:
        files = {"file": (filename, f, "text/csv")}
        resp = requests.post(f"{API_URL}/api/v1/files/upload", headers=headers, files=files)
    
    if resp.status_code != 201:
        print(f"Upload failed for {filename}: {resp.text}")
        return None
    
    data = resp.json()
    print(f"Uploaded {filename} ({row_count} rows) -> ID: {data['id']}")
    return data['id']

def run_pipeline(token, name, yaml_config):
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"name": name, "yaml_config": yaml_config}
    resp = requests.post(f"{API_URL}/api/v1/pipelines/run", headers=headers, json=payload)
    if resp.status_code != 202:
        print(f"Run failed: {resp.text}")
        return None
    return resp.json()["run_id"]

def wait_for_run(token, run_id):
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        resp = requests.get(f"{API_URL}/api/v1/pipelines/{run_id}", headers=headers)
        if resp.status_code != 200:
            print(f"Error fetching run: {resp.text}")
            return None
        data = resp.json()
        status = data.get("status")
        if status in ["COMPLETED", "FAILED", "CANCELLED"]:
            return data
        time.sleep(1)

def test_tier_routing(token):
    print("\n--- Testing Storage Tier Routing ---")
    f_small = upload_file(token, "small.csv", 1000)
    if f_small:
        yaml = f"""
pipeline:
  name: tier_small
  steps:
    - name: load
      type: load
      file_id: {f_small}
    - name: save
      type: save
      input: load
      filename: out_small
"""
        run_id = run_pipeline(token, "Small Tier", yaml)
        if run_id: wait_for_run(token, run_id)

    f_med = upload_file(token, "medium.csv", 1_000_000)
    if f_med:
        yaml = f"""
pipeline:
  name: tier_med
  steps:
    - name: load
      type: load
      file_id: {f_med}
    - name: save
      type: save
      input: load
      filename: out_med
"""
        run_id = run_pipeline(token, "Medium Tier", yaml)
        if run_id: wait_for_run(token, run_id)

    f_large_base = upload_file(token, "large_base.csv", 10_000)
    if f_large_base:
        yaml = f"""
pipeline:
  name: tier_large
  steps:
    - name: load
      type: load
      file_id: {f_large_base}
    - name: explode
      type: sql
      input: load
      query: "SELECT * FROM {{input}}, {{input}}"
    - name: save
      type: save
      input: explode
      filename: out_large
"""
        run_id = run_pipeline(token, "Large Tier", yaml)
        if run_id: wait_for_run(token, run_id)

def test_execution_routing(token):
    print("\n--- Testing Execution Routing (Pandas vs DuckDB) ---")
    f_id = upload_file(token, "route_small.csv", 1000)
    if f_id:
        yaml = f"""
pipeline:
  name: route_small
  steps:
    - name: load
      type: load
      file_id: {f_id}
    - name: filter
      type: filter
      input: load
      column: num
      operator: gt
      value: 500
    - name: save
      type: save
      input: filter
      filename: out_route_small
"""
        run_id = run_pipeline(token, "Route Small", yaml)
        if run_id: wait_for_run(token, run_id)

    f_id_l = upload_file(token, "route_large.csv", 100_000)
    if f_id_l:
        yaml = f"""
pipeline:
  name: route_large
  steps:
    - name: load
      type: load
      file_id: {f_id_l}
    - name: filter
      type: filter
      input: load
      column: num
      operator: gt
      value: 500
    - name: save
      type: save
      input: filter
      filename: out_route_large
"""
        run_id_l = run_pipeline(token, "Route Large", yaml)
        if run_id_l: wait_for_run(token, run_id_l)

def test_sql_step(token):
    print("\n--- Testing SQL Step ---")
    f_id = upload_file(token, "sql_test.csv", 1000)
    if f_id:
        yaml = f"""
pipeline:
  name: test_sql
  steps:
    - name: load
      type: load
      file_id: {f_id}
    - name: custom_sql
      type: sql
      input: load
      query: |
        SELECT id, val, num * 2 as num_x2 
        FROM {{input}} 
        WHERE num > 500
    - name: save
      type: save
      input: custom_sql
      filename: out_sql
"""
        run_id = run_pipeline(token, "SQL Run", yaml)
        if run_id: wait_for_run(token, run_id)

def test_sql_security(token):
    print("\n--- Testing SQL Security (Block DML/DDL) ---")
    f_id = upload_file(token, "sec_test.csv", 100)
    if f_id:
        yaml_drop = f"""
pipeline:
  name: test_drop
  steps:
    - name: load
      type: load
      file_id: {f_id}
    - name: malicious
      type: sql
      input: load
      query: "DROP TABLE __input__"
"""
        headers = {"Authorization": f"Bearer {token}"}
        val_resp = requests.post(f"{API_URL}/api/v1/pipelines/validate", headers=headers, json={"yaml_config": yaml_drop})
        print(f"DROP TABLE validation: {val_resp.status_code} - {val_resp.json().get('is_valid')}")

if __name__ == "__main__":
    token = get_token()
    if token:
        test_tier_routing(token)
        test_execution_routing(token)
        test_sql_step(token)
        test_sql_security(token)
    else:
        print("Failed to get token")
