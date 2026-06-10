import json
import requests
import time

BASE_URL = "http://localhost/api/v1"
AUTH_URL = "http://localhost/auth"


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

def test_drift():
    session = requests.Session()
    login_data = {"email": "demo@pipelineiq.app", "password": "Demo1234!"}
    resp = auth_request_with_retry(session, "login", login_data)
    assert resp.status_code == 200, f"Login failed: {resp.status_code} {resp.text}"
    token = resp.json()["access_token"]
    session.headers.update({"Authorization": f"Bearer {token}"})

    # 1. Upload original file
    print("Uploading original file...")
    with open("drift_v1.csv", "w") as f:
        f.write("id,name,amount\n1,Alice,100\n2,Bob,200\n")
    with open("drift_v1.csv", "rb") as f:
        resp = session.post(f"{BASE_URL}/files/upload", files={"file": ("drift_test.csv", f, "text/csv")})
    assert resp.status_code == 201
    print("V1 uploaded.")

    # 2. Upload modified file (Column removed, column added, type changed)
    # original: id (int), name (str), amount (float)
    # new: id (int), amount (str - will be object), discount (float)
    # removed: name
    print("Uploading modified file...")
    with open("drift_v2.csv", "w") as f:
        f.write("id,amount,discount\n1,one-hundred,0.1\n2,two-hundred,0.2\n")
    with open("drift_v2.csv", "rb") as f:
        resp = session.post(f"{BASE_URL}/files/upload", files={"file": ("drift_test.csv", f, "text/csv")})
    assert resp.status_code == 201
    drift = resp.json().get("schema_drift")
    print(f"Drift detected: {json.dumps(drift, indent=2)}")
     
    assert drift is not None
    assert drift["has_drift"] is True
    changes = drift.get("changes") or drift.get("drift_items") or []
    assert changes, "Expected schema drift change items in API response"
     
    # Check for breaking change (column removed)
    breaking = any(c["severity"] == "breaking" for c in changes)
    assert breaking, "Should have detected breaking change (column removed)"
     
    # Check for warning (type change)
    warning = any(c["severity"] == "warning" for c in changes)
    assert warning, "Should have detected warning (type change)"
     
    # Check for info (column added)
    info = any(c["severity"] == "info" for c in changes)
    assert info, "Should have detected info (column added)"

if __name__ == "__main__":
    try:
        test_drift()
        print("\nSchema Drift Test PASSED")
    except Exception as e:
        print(f"\nSchema Drift Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
