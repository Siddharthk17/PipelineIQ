import requests
import time
import uuid
import json
import os

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"
UPLOAD_DIR = "./test_uploads"

os.makedirs(UPLOAD_DIR, exist_ok=True)

def create_sample_csv(filename, rows=100):
    path = os.path.join(UPLOAD_DIR, filename)
    with open(path, "w") as f:
        f.write("order_id,amount,status,region\n")
        for i in range(rows):
            status = "delivered" if i % 2 == 0 else "pending"
            region = "North" if i % 3 == 0 else "South"
            f.write(f"{i},{i*10.5},{status},{region}\n")
    return path


def auth_request_with_retry(session, path, payload, attempts=5):
    for attempt in range(1, attempts + 1):
        resp = session.post(f"{AUTH_URL}/{path}", json=payload)
        if resp.status_code != 429:
            return resp
        if attempt < attempts:
            retry_after = resp.headers.get("Retry-After")
            backoff = int(retry_after) if retry_after and retry_after.isdigit() else 15
            print(f"{path} rate-limited, retrying in {backoff}s...")
            time.sleep(backoff)
    return resp

def test_lifecycle():
    session = requests.Session()
    
    # 1. Login as Demo User
    print("Logging in as demo user...")
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    resp = auth_request_with_retry(session, "login", login_data)
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("Logged in.")

    # 2. Upload File
    print("Uploading file...")
    csv_path = create_sample_csv("sales.csv")
    with open(csv_path, "rb") as f:
        files = {"file": ("sales.csv", f, "text/csv")}
        resp = session.post(f"{BASE_URL}/files/upload", files=files)
    assert resp.status_code == 201
    file_id = resp.json()["id"]
    print(f"File uploaded. ID: {file_id}")

    # 3. Run Pipeline
    print("Running pipeline...")
    pipeline_yaml = f"""
pipeline:
  name: e2e_test_pipeline
  steps:
    - name: load_data
      type: load
      file_id: {file_id}
    - name: filter_delivered
      type: filter
      input: load_data
      column: status
      operator: equals
      value: delivered
    - name: agg_region
      type: aggregate
      input: filter_delivered
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
    - name: save_result
      type: save
      input: agg_region
      filename: e2e_result
"""
    resp = session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": pipeline_yaml,
        "name": "E2E Test Run"
    })
    print(f"Run response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    print(f"Pipeline queued. Run ID: {run_id}")

    # 4. Poll for completion
    print("Polling for completion...")
    while True:
        resp = session.get(f"{BASE_URL}/pipelines/{run_id}")
        assert resp.status_code == 200
        status = resp.json()["status"]
        print(f"Current status: {status}")
        if status == "COMPLETED":
            break
        if status == "FAILED":
            print(f"Pipeline failed: {resp.json().get('error_message')}")
            assert False, "Pipeline failed"
        time.sleep(2)
    print("Pipeline completed.")

    # 5. Verify Lineage
    print("Checking lineage...")
    resp = session.get(f"{BASE_URL}/lineage/{run_id}")
    assert resp.status_code == 200
    graph = resp.json()
    assert "nodes" in graph and "edges" in graph
    print("Lineage graph verified.")

    # 6. Export Output
    print("Exporting output...")
    resp = session.get(f"{BASE_URL}/pipelines/{run_id}/export")
    assert resp.status_code == 200
    with open("e2e_output.csv", "wb") as f:
        f.write(resp.content)
    print("Output exported to e2e_output.csv")

if __name__ == "__main__":
    try:
        test_lifecycle()
        print("\nE2E API Lifecycle Test PASSED")
    except Exception as e:
        print(f"\nE2E API Lifecycle Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
