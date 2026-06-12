import requests
import pytest

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"


def _server_reachable():
    try:
        requests.get("http://localhost/healthz", timeout=2)
        return True
    except requests.ConnectionError:
        return False


@pytest.mark.skipif(not _server_reachable(), reason="Requires running Docker stack")
def test_public_access():
    # Login first
    resp = requests.post(f"{AUTH_URL}/login", json={"email": "demo@pipelineiq.app", "password": "Demo1234!"})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get run list
    resp = requests.get(f"{BASE_URL}/pipelines/", headers=headers)
    data = resp.json()
    runs = data.get("runs", [])
    if not runs:
        print("No runs found to test public access.")
        return

    run_id = runs[0]["id"]
    print(f"Testing access to run: {run_id}")

    # Get details with token
    resp = requests.get(f"{BASE_URL}/pipelines/{run_id}", headers=headers)
    print(f"GET /pipelines/{run_id} Response: {resp.status_code}")
    assert resp.status_code == 200

    # Get lineage with token
    resp = requests.get(f"{BASE_URL}/lineage/{run_id}", headers=headers)
    print(f"GET /lineage/{run_id} Response: {resp.status_code}")
    assert resp.status_code == 200

if __name__ == "__main__":
    test_public_access()
