import requests
import pandas as pd
import numpy as np
import io
import uuid
import time
import json

API_URL = "http://localhost:8000/api/v1"
AUTH_URL = "http://localhost:8000/auth"
USER_EMAIL = "demo@pipelineiq.app"
USER_PASS = "Demo1234!"


def get_token():
    resp = requests.post(
        f"{AUTH_URL}/login", json={"email": USER_EMAIL, "password": USER_PASS}
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def upload_file(token, filename, rows=1000):
    df = pd.DataFrame(
        {
            "id": range(rows),
            "val": np.random.randn(rows),
            "cat": np.random.choice(["A", "B", "C"], rows),
        }
    )
    csv_buffer = io.BytesIO()
    df.to_csv(csv_buffer, index=False)
    csv_buffer.seek(0)

    files = {"file": (filename, csv_buffer, "text/csv")}
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(f"{API_URL}/files/upload", headers=headers, files=files)
    resp.raise_for_status()
    return resp.json()["id"]


def run_pipeline(token, name, yaml_config):
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.post(
        f"{API_URL}/pipelines/run",
        headers=headers,
        json={"name": name, "yaml_config": yaml_config},
    )
    if resp.status_code == 400:
        print(f"Error 400 response: {resp.text}")
    resp.raise_for_status()
    return resp.json()["run_id"]


def wait_for_run(token, run_id):
    headers = {"Authorization": f"Bearer {token}"}
    while True:
        resp = requests.get(f"{API_URL}/pipelines/{run_id}", headers=headers)
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]
        if status in ["COMPLETED", "FAILED", "CANCELLED"]:
            return data
        time.sleep(1)


def test_week3():
    token = get_token()
    print("Authenticated successfully.")

    # 1. Small Data -> Pandas
    print("\nTesting Small Data (Pandas)...")
    fid_small = upload_file(token, "small.csv", rows=1000)
    yaml_small = f"""
pipeline:
  name: small_test
  steps:
    - name: load_small
      type: load
      file_id: {fid_small}
    - name: filter_small
      type: filter
      input: load_small
      column: val
      operator: greater_than
      value: 0
    - name: save_small
      type: save
      input: filter_small
      filename: small_out
"""
    run_id_small = run_pipeline(token, "Small Test", yaml_small)
    res_small = wait_for_run(token, run_id_small)
    print(f"Small run status: {res_small['status']}")
    assert res_small["status"] == "COMPLETED"

    # 2. Medium Data -> DuckDB
    print("\nTesting Medium Data (DuckDB)...")
    fid_medium = upload_file(token, "medium.csv", rows=100000)
    yaml_medium = f"""
pipeline:
  name: medium_test
  steps:
    - name: load_medium
      type: load
      file_id: {fid_medium}
    - name: filter_medium
      type: filter
      input: load_medium
      column: val
      operator: greater_than
      value: 0
    - name: save_medium
      type: save
      input: filter_medium
      filename: medium_out
"""
    run_id_medium = run_pipeline(token, "Medium Test", yaml_medium)
    res_medium = wait_for_run(token, run_id_medium)
    print(f"Medium run status: {res_medium['status']}")
    assert res_medium["status"] == "COMPLETED"

    # 3. SQL Step
    print("\nTesting SQL Step...")
    yaml_sql = f"""
pipeline:
  name: sql_test
  steps:
    - name: load_sql
      type: load
      file_id: {fid_medium}
    - name: query_sql
      type: sql
      input: load_sql
      query: |
        SELECT cat, AVG(val) as avg_val 
        FROM {{{{input}}}} 
        GROUP BY cat
    - name: save_sql
      type: save
      input: query_sql
      filename: sql_out
"""
    run_id_sql = run_pipeline(token, "SQL Test", yaml_sql)
    res_sql = wait_for_run(token, run_id_sql)
    print(f"SQL run status: {res_sql['status']}")
    assert res_sql["status"] == "COMPLETED"

    # 4. SQL validation: keywords inside literals are allowed
    print("\nTesting SQL literal keyword allowance...")
    yaml_literal_keyword = f"""
pipeline:
  name: literal_keyword_test
  steps:
    - name: load_literal
      type: load
      file_id: {fid_medium}
    - name: literal_sql
      type: sql
      input: load_literal
      query: "SELECT * FROM {{{{input}}}} WHERE 1=1 AND 'drop' = 'drop'"
    - name: save_literal
      type: save
      input: literal_sql
      filename: literal_out
"""
    allowed_resp = requests.post(
        f"{API_URL}/pipelines/run",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Literal Keyword Test", "yaml_config": yaml_literal_keyword},
    )
    print(f"Literal keyword response: {allowed_resp.text}")
    print(f"Literal keyword status: {allowed_resp.status_code}")
    assert allowed_resp.status_code == 202
    allowed_run_id = allowed_resp.json()["run_id"]
    allowed_result = wait_for_run(token, allowed_run_id)
    assert allowed_result["status"] == "COMPLETED"

    # 5. SQL Injection / Security: disallowed statements are blocked
    print("\nTesting SQL injection prevention...")
    yaml_malicious = f"""
pipeline:
  name: malicious_test
  steps:
    - name: load_mal
      type: load
      file_id: {fid_medium}
    - name: attack_sql
      type: sql
      input: load_mal
      query: "SELECT * FROM {{{{input}}}}; DROP TABLE users"
    - name: save_mal
      type: save
      input: attack_sql
      filename: mal_out
"""
    blocked_resp = requests.post(
        f"{API_URL}/pipelines/run",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Malicious Test", "yaml_config": yaml_malicious},
    )
    print(f"Malicious run response: {blocked_resp.text}")
    print(f"Malicious run response status: {blocked_resp.status_code}")
    assert blocked_resp.status_code == 400
    assert "disallowed" in blocked_resp.text.lower() or "single sql statement" in blocked_resp.text.lower()

    print("\nALL FUNCTIONAL TESTS PASSED!")


if __name__ == "__main__":
    test_week3()
