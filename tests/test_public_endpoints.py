import requests

BASE_URL = "http://localhost/api/v1"

def test_public_access():
    # We need a run_id. Let's find one from the list.
    resp = requests.get(f"{BASE_URL}/pipelines/")
    if not resp.json()["runs"]:
        print("No runs found to test public access.")
        return
    
    run_id = resp.json()["runs"][0]["id"]
    print(f"Testing public access to run: {run_id}")
    
    # Try to get details without token
    resp = requests.get(f"{BASE_URL}/pipelines/{run_id}")
    print(f"GET /pipelines/{run_id} Response: {resp.status_code}")
    
    # Try to get lineage without token
    resp = requests.get(f"{BASE_URL}/lineage/{run_id}")
    print(f"GET /lineage/{run_id} Response: {resp.status_code}")

if __name__ == "__main__":
    test_public_access()
