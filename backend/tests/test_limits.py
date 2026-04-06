import requests
import uuid
import pytest

BASE_URL = "http://localhost:8000/api/v1"
AUTH_URL = "http://localhost:8000/auth"


@pytest.fixture(scope="module")
def auth_token():
    email = f"limit_{uuid.uuid4().hex[:8]}@test.com"
    username = f"limit_{uuid.uuid4().hex[:8]}"
    password = "Str0ngP@ss123!"

    requests.post(
        f"{AUTH_URL}/register",
        json={"email": email, "username": username, "password": password},
    )

    login_resp = requests.post(
        f"{AUTH_URL}/login", json={"email": email, "password": password}
    )
    return login_resp.json()["access_token"]


def test_pipeline_step_count_limit(auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}

    # Create a pipeline with 51 steps (limit is 50)
    steps = []
    # Step 0: Load
    steps.append({"name": "load", "type": "load", "file_id": "some-uuid"})
    # Steps 1-50: Filter (total 51 steps)
    for i in range(1, 51):
        steps.append(
            {
                "name": f"step_{i}",
                "type": "filter",
                "input": "load" if i == 1 else f"step_{i - 1}",
                "column": "col",
                "operator": "equals",
                "value": "val",
            }
        )

    pipeline_yaml = (
        f"pipeline:\n  name: too_many_steps\n  steps:\n    - "
        + "\n    - ".join([str(s) for s in steps])
    )
    # Note: The above is a bit messy, I'll use a proper YAML dump.

    import yaml

    config = {"pipeline": {"name": "too_many_steps", "steps": steps}}
    yaml_string = yaml.dump(config)

    resp = requests.post(
        f"{BASE_URL}/pipelines/validate",
        headers=headers,
        json={"yaml_config": yaml_string},
    )

    assert resp.status_code == 200  # Validation returns 200 but is_valid=False
    print(f"Errors: {resp.json()['errors']}")
    assert resp.json()["is_valid"] is False
    assert any(
        "maximum" in err["message"].lower()
        or "too many" in err["message"].lower()
        or "exceeding the limit" in err["message"].lower()
        for err in resp.json()["errors"]
    )
