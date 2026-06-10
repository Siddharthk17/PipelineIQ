import requests
import time
import uuid

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


def register_user(username, email, password):
    resp = auth_request_with_retry(
        "register",
        {"username": username, "email": email, "password": password},
    )
    if resp.status_code != 201:
        print(f"Register failed: {resp.status_code} {resp.text}")
    return resp.json()


def login(email, password):
    resp = auth_request_with_retry("login", {"email": email, "password": password})
    if resp.status_code != 200:
        print(f"Login failed: {resp.status_code} {resp.text}")
        return None, None
    payload = resp.json()
    return payload.get("access_token"), payload.get("user", {}).get("id")


def get_token_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_isolation():
    # Admin session is used only to grant explicit per-pipeline runner access.
    admin_token, _ = login("demo@pipelineiq.app", "Demo1234!")
    assert admin_token is not None

    # 1. Setup Users
    u1_id = uuid.uuid4().hex[:8]
    u2_id = uuid.uuid4().hex[:8]
    u1_email = f"user1_{u1_id}@test.com"
    u2_email = f"user2_{u2_id}@test.com"

    register_user(f"user1_{u1_id}", u1_email, "Pass123!")
    register_user(f"user2_{u2_id}", u2_email, "Pass123!")

    t1, user1_id = login(u1_email, "Pass123!")
    t2, _ = login(u2_email, "Pass123!")

    print(f"User 1 Token: {t1[:10]}...")
    print(f"User 2 Token: {t2[:10]}...")

    # 2. User 1 Uploads a File
    with open("test_isolation.csv", "w") as f:
        f.write("col1,col2\n1,2\n3,4")

    with open("test_isolation.csv", "rb") as upload_file:
        files = {"file": ("test.csv", upload_file, "text/csv")}
        resp = requests.post(
            f"{BASE_URL}/files/upload", headers=get_token_header(t1), files=files
        )
    if resp.status_code != 201:
        print(f"Upload failed: {resp.status_code} {resp.text}")
    assert resp.status_code == 201
    file_id = resp.json()["id"]
    print(f"User 1 uploaded file: {file_id}")

    # Grant the minimum permission required for User 1 to execute this pipeline.
    permission_resp = requests.post(
        f"{BASE_URL}/pipelines/isolation_test/permissions",
        headers=get_token_header(admin_token),
        json={"user_id": user1_id, "permission_level": "runner"},
    )
    assert permission_resp.status_code in {200, 201}, permission_resp.text

    # 3. User 2 tries to access User 1's file
    resp = requests.get(f"{BASE_URL}/files/{file_id}", headers=get_token_header(t2))
    print(f"User 2 accessing User 1 file: {resp.status_code}")
    assert resp.status_code in {403, 404}, "User 2 should not see User 1's file"

    # 4. User 1 runs a pipeline
    yaml_config = f"""
pipeline:
  name: isolation_test
  steps:
    - name: load
      type: load
      file_id: {file_id}
    - name: save
      type: save
      input: load
      filename: isolation_output
"""
    resp = requests.post(
        f"{BASE_URL}/pipelines/run",
        headers=get_token_header(t1),
        json={"yaml_config": yaml_config, "name": "isolation_test"},
    )
    if resp.status_code != 202:
        print(f"Run failed: {resp.status_code} {resp.text}")
    assert resp.status_code == 202
    run_id = resp.json()["run_id"]
    print(f"User 1 started run: {run_id}")

    # 5. User 2 tries to access User 1's run
    resp = requests.get(f"{BASE_URL}/pipelines/{run_id}", headers=get_token_header(t2))
    print(f"User 2 accessing User 1 run: {resp.status_code}")
    assert resp.status_code in {403, 404}, "User 2 should not see User 1's run"

    # 6. User 2 tries to access User 1's lineage
    resp = requests.get(f"{BASE_URL}/lineage/{run_id}", headers=get_token_header(t2))
    print(f"User 2 accessing User 1 lineage: {resp.status_code}")
    assert resp.status_code in {403, 404}, "User 2 should not see User 1's lineage"

    print("\n✅ Isolation verification successful!")


if __name__ == "__main__":
    import traceback

    try:
        test_isolation()
    except Exception as e:
        print(f"\n❌ Isolation verification failed: {e}")
        traceback.print_exc()
        exit(1)
