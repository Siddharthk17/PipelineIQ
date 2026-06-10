import requests
import uuid
import time
import json

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"

def auth_request_with_retry(path, payload, attempts=5):
    for attempt in range(1, attempts + 1):
        resp = requests.post(f"{AUTH_URL}/{path}", json=payload)
        if resp.status_code != 429:
            return resp
        if attempt < attempts:
            retry_after = resp.headers.get("Retry-After")
            backoff = int(retry_after) if retry_after and retry_after.isdigit() else 15
            print(f"{path} rate-limited, retrying in {backoff}s...")
            time.sleep(backoff)
    return resp


def register_and_login(username, email, password):
    register_resp = auth_request_with_retry(
        "register",
        {"username": username, "email": email, "password": password},
    )
    assert register_resp.status_code in {200, 201}, (
        f"Register failed: {register_resp.status_code} {register_resp.text}"
    )
    login_resp = auth_request_with_retry("login", {"email": email, "password": password})
    assert login_resp.status_code == 200, (
        f"Login failed: {login_resp.status_code} {login_resp.text}"
    )
    return login_resp.json().get("access_token")

def test_circular_dependency():
    print("Testing circular dependency...")
    unique = uuid.uuid4().hex[:8]
    token = register_and_login(
        f"circular_{unique}",
        f"circular_{unique}@test.com",
        "Pass123!",
    )
    yaml_config = """
pipeline:
  name: circular_test
  steps:
    - name: step1
      type: filter
      input: step2
      column: col1
      operator: equals
      value: 1
    - name: step2
      type: filter
      input: step1
      column: col1
      operator: equals
      value: 1
"""
    resp = requests.post(
        f"{BASE_URL}/pipelines/validate",
        headers={"Authorization": f"Bearer {token}"},
        json={"yaml_config": yaml_config}
    )
    print(f"Circular dep status: {resp.status_code}, body: {resp.text}")
    assert resp.status_code == 200
    assert resp.json()["is_valid"] is False

def test_non_existent_file():
    print("\nTesting non-existent file ID...")
    unique = uuid.uuid4().hex[:8]
    token = register_and_login(
        f"nonexistent_{unique}",
        f"nonexistent_{unique}@test.com",
        "Pass123!",
    )
    resp = requests.post(
        f"{BASE_URL}/pipelines/run",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "yaml_config": "pipeline:\n  name: test\n  steps:\n    - name: load\n      type: load\n      file_id: '00000000-0000-0000-0000-000000000000'",
            "name": "non_existent_run"
        }
    )
    print(f"Non-existent file status: {resp.status_code}, body: {resp.text}")
    assert resp.status_code in {403, 404}

if __name__ == "__main__":
    try:
        test_circular_dependency()
        test_non_existent_file()
        print("\n✅ Edge case tests passed!")
    except Exception as e:
        print(f"\n❌ Edge case tests failed: {e}")
        exit(1)
