import requests
import time

TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJiOTI3ZmQ5ZS0yNGUwLTQ3NDgtOWUyZS04ZDcxZGZlOTE5NmMiLCJyb2xlIjoiYWRtaW4iLCJleHAiOjE3NzYwMDIxOTB9.fPFkPrrw1T9UbzCg8WBdxBHI_cztEpNpvzaA1QjA8pE"
BASE_URL = "http://localhost"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

yaml_stress = """
pipeline:
  name: stress_test
  steps:
    - name: load_large
      type: load
      file_id: "0290fed0-47b1-4d4b-9337-fb29e8f082f7"
    - name: complex_sql
      type: sql
      input: load_large
      query: "SELECT cat, AVG(val) as avg_val, COUNT(*) as cnt FROM {{input}} GROUP BY cat ORDER BY avg_val DESC"
    - name: save_stress
      type: save
      input: complex_sql
      filename: stress_output
"""

resp = requests.post(f"{BASE_URL}/api/v1/pipelines/run", headers=HEADERS, json={
    "yaml_config": yaml_stress,
    "name": "Stress Test Run"
})

if resp.status_code == 202:
    run_id = resp.json()["run_id"]
    print(f"Started stress test, run_id: {run_id}")
    while True:
        status_resp = requests.get(f"{BASE_URL}/api/v1/pipelines/{run_id}", headers=HEADERS)
        status = status_resp.json().get("status")
        print(f"Status: {status}")
        if status in ["COMPLETED", "FAILED", "CANCELLED"]:
            break
        time.sleep(2)
else:
    print(f"Error: {resp.text}")
