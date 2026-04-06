import pytest
import pandas as pd
import numpy as np
import io
from fastapi.testclient import TestClient
from backend.main import app
from backend.dependencies import get_db, get_read_db, get_write_db
from sqlalchemy.orm import Session
from backend.auth import get_current_user
from backend.models import User, UploadedFile
import uuid as _uuid
from backend.config import settings
import yaml


def set_current_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def stress_client(test_db: Session, tmp_path):
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db
    app.dependency_overrides[get_write_db] = override_get_db

    from backend.api.files import settings as file_settings

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    file_settings.UPLOAD_DIR = upload_dir
    file_settings.ALLOWED_EXTENSIONS = {".csv", ".json"}
    file_settings.MAX_UPLOAD_SIZE = 500 * 1024 * 1024
    file_settings.MAX_ROWS_PER_FILE = 1000000

    from backend.api.pipelines import execute_pipeline_task
    from unittest.mock import MagicMock

    execute_pipeline_task.delay = MagicMock(return_value=MagicMock(id="mock-task-id"))

    return TestClient(app)


@pytest.fixture()
def admin_user(test_db: Session):
    user = User(
        id=_uuid.uuid4(),
        email="extreme@test.com",
        username="extreme_admin",
        hashed_password="hashed_password",
        role="admin",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    return user


def test_upload_max_rows_edge_case(stress_client, admin_user, test_db):
    set_current_user(admin_user)

    # 1M rows
    df = pd.DataFrame({"id": np.arange(1000000), "val": np.random.randn(1000000)})
    csv_bytes = df.to_csv(index=False).encode()

    response = stress_client.post(
        "/api/v1/files/upload",
        files={"file": ("max_rows.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["row_count"] == 1000000


def test_pipeline_max_steps_limit(stress_client, admin_user, test_db):
    set_current_user(admin_user)

    # Create a file to load
    file_id = str(_uuid.uuid4())
    test_db.add(
        UploadedFile(
            id=_uuid.uuid4(),
            original_filename="test.csv",
            stored_path="/tmp/test.csv",
            file_size_bytes=100,
            row_count=1,
            column_count=1,
            columns=["col1"],
            dtypes={"col1": "int64"},
            user_id=admin_user.id,
        )
    )
    test_db.commit()
    actual_file_id = str(test_db.query(UploadedFile).first().id)

    # Build a pipeline with 50 steps (MAX_PIPELINE_STEPS)
    steps = []
    # Step 0: load
    steps.append({"name": "step_0", "type": "load", "file_id": actual_file_id})
    # Steps 1-49: select (passthrough)
    for i in range(1, 50):
        steps.append(
            {
                "name": f"step_{i}",
                "type": "select",
                "input": f"step_{i - 1}",
                "columns": ["col1"],
            }
        )

    config_dict = {"pipeline": {"name": "max_steps_pipeline", "steps": steps}}
    yaml_string = yaml.dump(config_dict)

    response = stress_client.post(
        "/api/v1/pipelines/validate",
        json={"yaml_config": yaml_string},
    )
    assert response.status_code == 200
    assert response.json()["is_valid"] is True

    # Test 51 steps (should fail)
    steps.append(
        {"name": "step_50", "type": "select", "input": "step_49", "columns": ["col1"]}
    )
    config_dict["pipeline"]["steps"] = steps
    yaml_string_over = yaml.dump(config_dict)

    response_over = stress_client.post(
        "/api/v1/pipelines/validate",
        json={"yaml_config": yaml_string_over},
    )
    assert response_over.status_code == 200
    assert response_over.json()["is_valid"] is False
    assert any(
        "exceeding the limit" in e["message"].lower()
        for e in response_over.json()["errors"]
    )
