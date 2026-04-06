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

def test_permissions():
    admin_session = requests.Session()
    viewer_session = requests.Session()
    
    # 1. Setup Admin (Demo)
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    resp = auth_request_with_retry(admin_session, "login", login_data)
    assert resp.status_code == 200, f"Admin login failed: {resp.status_code} {resp.text}"
    admin_token = resp.json()["access_token"]
    admin_session.headers.update({"Authorization": f"Bearer {admin_token}"})
    
    # 2. Setup Viewer
    viewer_email = f"viewer_{uuid.uuid4().hex[:8]}@example.com"
    viewer_username = f"viewer_{uuid.uuid4().hex[:8]}"
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
    viewer_id = resp.json()["user"]["id"]
    
    # 3. Viewer tries to run a random pipeline (should fail)
    print("Testing viewer run attempt (should fail)...")
    pipeline_yaml = "pipeline:\n  name: test_pipeline\n  steps: []" # minimal invalid yaml just to check auth
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": pipeline_yaml,
        "name": "test_pipeline"
    })
    # It might fail with 400 because of empty steps, but it should NOT be 202.
    # Actually, let's use a valid one.
    
    # Create a valid file first
    with open("test_perm.csv", "w") as f:
        f.write("id,val\n1,10\n")
    with open("test_perm.csv", "rb") as f:
        resp = admin_session.post(f"{BASE_URL}/files/upload", files={"file": ("test_perm.csv", f, "text/csv")})
    file_id = resp.json()["id"]
    
    valid_yaml = f"pipeline:\n  name: perm_test\n  steps:\n    - name: load\n      type: load\n      file_id: {file_id}\n    - name: save\n      type: save\n      input: load\n      filename: out"

    print("Testing viewer run attempt with valid YAML (should fail 403)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "perm_test"
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 403, "Viewer should not be able to run pipeline without permission"

    # 4. Admin grants Runner permission
    print("Granting runner permission...")
    resp = admin_session.post(f"{BASE_URL}/pipelines/perm_test/permissions", json={
        "user_id": viewer_id,
        "permission_level": "runner"
    })
    assert resp.status_code == 201 or resp.status_code == 200
    print("Permission granted.")

    # 5. Viewer tries to run the SAME pipeline (should succeed)
    print("Testing viewer run attempt with permission (should succeed 202)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "perm_test"
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 202, "Viewer with runner permission should be able to run"

    # 6. Viewer tries to run a DIFFERENT pipeline (should fail 403)
    print("Testing viewer run attempt on different pipeline (should fail 403)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml.replace("name: perm_test", "name: other_pipeline"),
        "name": "other_pipeline"
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 403, "Viewer should not be able to run other pipelines"

    # 7. Admin revokes permission
    print("Revoking permission...")
    resp = admin_session.delete(f"{BASE_URL}/pipelines/perm_test/permissions/{viewer_id}")
    assert resp.status_code == 200 or resp.status_code == 204
    print("Permission revoked.")

    # 8. Viewer tries again (should fail 403)
    print("Testing viewer run attempt after revocation (should fail 403)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": valid_yaml,
        "name": "perm_test"
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 403, "Viewer should not be able to run after revocation"

if __name__ == "__main__":
    try:
        test_permissions()
        print("\nPermissions Test PASSED")
    except Exception as e:
        print(f"\nPermissions Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
