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

def test_read_permissions():
    admin_session = requests.Session()
    viewer_session = requests.Session()
    
    # 1. Setup Admin (Demo)
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    resp = auth_request_with_retry(admin_session, "login", login_data)
    assert resp.status_code == 200, f"Admin login failed: {resp.status_code} {resp.text}"
    admin_token = resp.json()["access_token"]
    admin_session.headers.update({"Authorization": f"Bearer {admin_token}"})
    
    # 2. Setup Viewer
    viewer_email = f"viewer_read_{uuid.uuid4().hex[:8]}@example.com"
    viewer_username = f"viewer_read_{uuid.uuid4().hex[:8]}"
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
    
    # 3. Create a pipeline run
    # We need a file first
    with open("test_read.csv", "w") as f:
        f.write("id,val\n1,10\n")
    with open("test_read.csv", "rb") as f:
        resp = admin_session.post(f"{BASE_URL}/files/upload", files={"file": ("test_read.csv", f, "text/csv")})
    file_id = resp.json()["id"]
    
    valid_yaml = f"pipeline:\n  name: read_test\n  steps:\n    - name: load\n      type: load\n      file_id: {file_id}\n    - name: save\n      type: save\n      input: load\n      filename: out"
    
    resp = admin_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "read_test"
    })
    run_id = resp.json()["run_id"]
    
    # Wait for completion
    while True:
        resp = admin_session.get(f"{BASE_URL}/pipelines/{run_id}")
        if resp.json()["status"] == "COMPLETED":
            break
        time.sleep(1)

    # 4. Test Export (Should fail for Viewer without permission)
    print("Testing export for Viewer without permission (should fail 403)...")
    resp = viewer_session.get(f"{BASE_URL}/pipelines/{run_id}/export")
    print(f"Export Response: {resp.status_code}")
    assert resp.status_code == 403, "Viewer should not be allowed to export another user's run"
    
    # 5. Test Cancel (Should fail for Viewer)
    print("Testing cancel for Viewer without permission (should fail 403)...")
    # We need a running pipeline for this. Let's run another one.
    resp = admin_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "cancel_test"
    })
    cancel_run_id = resp.json()["run_id"]
    
    resp = viewer_session.post(f"{BASE_URL}/pipelines/{cancel_run_id}/cancel")
    print(f"Cancel Response: {resp.status_code}")
    assert resp.status_code == 403, "Viewer should not be allowed to cancel another user's run"

if __name__ == "__main__":
    test_read_permissions()
