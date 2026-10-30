import os
import tempfile
import time
import uuid

import pytest
import requests

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"


def _server_reachable():
    try:
        requests.get("http://localhost/healthz", timeout=2)
    except requests.ConnectionError:
        return False


    else:
        return True
def auth_request_with_retry(session, path, payload, attempts=8):
    for attempt in range(1, attempts + 1):
        resp = session.post(f"{AUTH_URL}/{path}", json=payload)
        if resp.status_code != 429:
            return resp
        if attempt < attempts:
            backoff = min(2 ** attempt, 30)
            print(f"{path} rate-limited, retrying in {backoff}s...")
            time.sleep(backoff)
    return resp


def _upload_csv(session, content=b"id,val\n1,10\n"):
    with tempfile.NamedTemporaryFile(
            suffix=".csv", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        with open(tmp_path, "rb") as f:
            resp = session.post(
                f"{BASE_URL}/files/upload",
                files={"file": (os.path.basename(tmp_path), f, "text/csv")},
            )
    finally:
        os.unlink(tmp_path)
    assert resp.status_code == 201, f"Upload failed: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def _pipeline_yaml(name, file_id):
    return (
        f"pipeline:\n  name: {name}\n  steps:\n"
        f"    - name: load\n      type: load\n      file_id: {file_id}\n"
        f"    - name: save\n      type: save\n      input: load\n      filename: out"
    )


@pytest.mark.skipif(not _server_reachable(), reason="Requires running Docker stack")
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

    tag = uuid.uuid4().hex[:8]
    viewer_file_id = _upload_csv(viewer_session)
    admin_file_id = _upload_csv(admin_session)

    # 3. Viewer runs a brand-new pipeline name (self-serve: becomes owner).
    own_name = f"viewer_own_{tag}"
    print("Testing viewer self-serve run on a new name (should succeed 202)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": _pipeline_yaml(own_name, viewer_file_id),
        "name": own_name,
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 202, "Viewer should be able to run a brand-new pipeline"

    # 4. Admin runs their own pipeline; viewer has no grant on it.
    admin_name = f"admin_owned_{tag}"
    resp = admin_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": _pipeline_yaml(admin_name, admin_file_id),
        "name": admin_name,
    })
    assert resp.status_code == 202, f"Admin run failed: {resp.status_code} {resp.text}"

    # 5. Viewer tries the admin's pipeline without a grant (should fail 403).
    print("Testing viewer run on someone else's pipeline (should fail 404)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": _pipeline_yaml(admin_name, viewer_file_id),
        "name": admin_name,
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 404, "Viewer should not run another user's pipeline"

    # 6. Admin grants Runner permission.
    print("Granting runner permission...")
    resp = admin_session.post(f"{BASE_URL}/pipelines/{admin_name}/permissions", json={
        "user_id": viewer_id,
        "permission_level": "runner"
    })
    assert resp.status_code == 201 or resp.status_code == 200
    print("Permission granted.")

    # 7. Viewer runs the shared pipeline (should succeed 202).
    print("Testing viewer run with permission (should succeed 202)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": _pipeline_yaml(admin_name, viewer_file_id),
        "name": admin_name,
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 202, "Viewer with runner permission should be able to run"

    # 8. Admin revokes permission.
    print("Revoking permission...")
    resp = admin_session.delete(f"{BASE_URL}/pipelines/{admin_name}/permissions/{viewer_id}")
    assert resp.status_code == 200 or resp.status_code == 204
    print("Permission revoked.")

    # 9. Viewer tries again (should fail 403).
    print("Testing viewer run after revocation (should fail 404)...")
    resp = viewer_session.post(f"{BASE_URL}/pipelines/run", json={
        "yaml_config": _pipeline_yaml(admin_name, viewer_file_id),
        "name": admin_name,
    })
    print(f"Response: {resp.status_code} - {resp.text}")
    assert resp.status_code == 404, "Viewer should not be able to run after revocation"

if __name__ == "__main__":
    try:
        test_permissions()
        print("\nPermissions Test PASSED")
    except Exception as e:
        print(f"\nPermissions Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
