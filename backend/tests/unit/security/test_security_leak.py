import requests
import time
import uuid

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"


def auth_request_with_retry(session, path, payload, attempts=5):
    for attempt in range(1, attempts + 1):
        resp = session.post(f"{AUTH_URL}/{path}", json=payload)
        if resp.status_code != 429:
            return resp
        if attempt < attempts:
            backoff = attempt * 2
            print(f"{path} rate-limited, retrying in {backoff}s...")
            time.sleep(backoff)
    return resp

def test_leak():
    # 1. Admin creates a run
    admin_session = requests.Session()
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    resp = auth_request_with_retry(admin_session, "login", login_data)
    assert resp.status_code == 200, f"Admin login failed: {resp.status_code} {resp.text}"
    admin_token = resp.json()["access_token"]
    admin_session.headers.update({"Authorization": f"Bearer {admin_token}"})
    
    # Need a file first
    with open("leak_test.csv", "w") as f:
        f.write("id,val\n1,10\n")
    with open("leak_test.csv", "rb") as f:
        resp = admin_session.post(f"{BASE_URL}/files/upload", files={"file": ("leak_test.csv", f, "text/csv")})
    file_id = resp.json()["id"]
    
    valid_yaml = f"pipeline:\n  name: leak_test\n  steps:\n    - name: load\n      type: load\n      file_id: {file_id}\n    - name: save\n      type: save\n      input: load\n      filename: out"
    
    resp = admin_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "Leak Test Run"
    })
    run_id = resp.json()["run_id"]
    print(f"Admin created run: {run_id}")

    # 2. Another user tries to access this run
    viewer_session = requests.Session()
    viewer_email = f"leaker_{uuid.uuid4().hex[:8]}@example.com"
    viewer_username = f"leaker_{uuid.uuid4().hex[:8]}"
    resp = auth_request_with_retry(viewer_session, "register", {
        "email": viewer_email,
        "username": viewer_username,
        "password": "Password123!"
    })
    assert resp.status_code in {200, 201}, f"Viewer register failed: {resp.status_code} {resp.text}"
    resp = auth_request_with_retry(
        viewer_session,
        "login",
        {"email": viewer_email, "password": "Password123!"},
    )
    assert resp.status_code == 200, f"Viewer login failed: {resp.status_code} {resp.text}"
    viewer_token = resp.json()["access_token"]
    viewer_session.headers.update({"Authorization": f"Bearer {viewer_token}"})
    
    print(f"Testing access to run {run_id} as {viewer_username}...")
    resp = viewer_session.get(f"{BASE_URL}/pipelines/{run_id}")
    print(f"Response: {resp.status_code}")
    
    assert resp.status_code in {401, 403, 404}, (
        "Security leak: another user can access a pipeline run they do not own"
    )
    print("Access denied as expected.")

if __name__ == "__main__":
    test_leak()
