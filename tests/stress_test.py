import requests
import time
from concurrent.futures import ThreadPoolExecutor

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"

def create_large_csv(filename, rows=100000):
    path = f"./{filename}"
    with open(path, "w") as f:
        f.write("order_id,amount,status,region\n")
        for i in range(rows):
            status = "delivered" if i % 2 == 0 else "pending"
            region = "North" if i % 3 == 0 else "South"
            f.write(f"{i},{i*10.5},{status},{region}\n")
    return path

def run_pipeline(session, file_id, run_index):
    pipeline_yaml = f"""
pipeline:
  name: stress_test_{run_index}
  steps:
    - name: load
      type: load
      file_id: {file_id}
    - name: filter
      type: filter
      input: load
      column: status
      operator: equals
      value: delivered
    - name: agg
      type: aggregate
      input: filter
      group_by: [region]
      aggregations:
        - column: amount
          function: sum
    - name: save
      type: save
      input: agg
      filename: stress_out_{run_index}
"""
    resp = session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": pipeline_yaml,
        "name": f"Stress Run {run_index}"
    })
    body = {}
    try:
        body = resp.json()
    except ValueError:
        pass
    return resp.status_code, body.get("run_id"), resp.text


def login_with_retry(session, attempts=8):
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    for attempt in range(1, attempts + 1):
        resp = session.post(f"{AUTH_URL}/login", json=login_data)
        if resp.status_code == 200 and "access_token" in resp.json():
            return resp.json()["access_token"]
        if resp.status_code == 429 and attempt < attempts:
            retry_after = resp.headers.get("Retry-After")
            backoff = int(retry_after) if retry_after and retry_after.isdigit() else 15
            print(f"Login rate-limited, retrying in {backoff}s...")
            time.sleep(backoff)
            continue
        raise AssertionError(f"Login failed ({resp.status_code}): {resp.text}")
    raise AssertionError("Login failed after retries")

def test_stress():
    session = requests.Session()
    token = login_with_retry(session)
    session.headers.update({"Authorization": f"Bearer {token}"})

    # Upload 100k row file
    print("Uploading 100k row file...")
    path = create_large_csv("stress_test.csv")
    with open(path, "rb") as f:
        resp = session.post(f"{BASE_URL}/files/upload", files={"file": ("stress_test.csv", f, "text/csv")})
    assert resp.status_code == 201
    file_id = resp.json()["id"]
    print(f"File uploaded. ID: {file_id}")

    # Run 10 pipelines concurrently
    print("Running 10 pipelines concurrently...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda i: run_pipeline(session, file_id, i), range(10)))
    
    status_codes = [result[0] for result in results]
    run_ids = [result[1] for result in results if result[0] == 202 and result[1]]
    print(f"Responses: {status_codes}")
    assert all(code == 202 for code in status_codes), f"Some pipelines failed to queue: {results}"
    assert len(run_ids) == 10, f"Expected 10 run IDs, got {len(run_ids)}"
    
    # Wait for all to complete
    print("Waiting for completions...")
    completed = 0
    failed = 0
    deadline = time.time() + 900
    while completed + failed < len(run_ids):
        completed = 0
        failed = 0
        for run_id in run_ids:
            resp = session.get(f"{BASE_URL}/pipelines/{run_id}")
            assert resp.status_code == 200, f"Failed to fetch run {run_id}: {resp.status_code} {resp.text}"
            status = resp.json().get("status")
            if status == "COMPLETED":
                completed += 1
            elif status in {"FAILED", "CANCELLED"}:
                failed += 1
        print(f"Completed: {completed}/{len(run_ids)} | Failed: {failed}/{len(run_ids)}")
        if time.time() > deadline:
            raise AssertionError("Stress test timed out waiting for pipeline completion")
        time.sleep(5)
    assert failed == 0, f"Some pipelines failed or were cancelled: {failed}/{len(run_ids)}"

    print("Stress test completed successfully.")

if __name__ == "__main__":
    test_stress()
