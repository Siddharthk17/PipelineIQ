import pytest
import pandas as pd
import numpy as np
import io
from fastapi.testclient import TestClient
from backend.main import app
from backend.dependencies import get_db, get_read_db, get_write_db
from sqlalchemy.orm import Session
from backend.auth import get_current_user
from backend.models import User
import uuid as _uuid


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
    file_settings.MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # Increase for stress test
    file_settings.MAX_ROWS_PER_FILE = 1000000

    from backend.api.pipelines import execute_pipeline_task
    from unittest.mock import MagicMock

    execute_pipeline_task.delay = MagicMock(return_value=MagicMock(id="mock-task-id"))

    return TestClient(app)


@pytest.fixture()
def admin_user(test_db: Session):
    user = User(
        id=_uuid.uuid4(),
        email="stress@test.com",
        username="stress_admin",
        hashed_password="hashed_password",
        role="admin",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    return user


def test_upload_max_rows_limit(stress_client, admin_user, test_db):
    set_current_user(admin_user)

    # 1. Test exactly 1,000,000 rows (should pass)
    df_limit = pd.DataFrame({"id": np.arange(1000000), "val": np.random.randn(1000000)})
    csv_bytes = df_limit.to_csv(index=False).encode()

    response = stress_client.post(
        "/api/v1/files/upload",
        files={"file": ("limit.csv", csv_bytes, "text/csv")},
    )
    assert response.status_code == 201

    # 2. Test 1,000,001 rows (should fail)
    df_over = pd.DataFrame({"id": np.arange(1000001), "val": np.random.randn(1000001)})
    csv_bytes_over = df_over.to_csv(index=False).encode()

    response = stress_client.post(
        "/api/v1/files/upload",
        files={"file": ("over.csv", csv_bytes_over, "text/csv")},
    )
    assert response.status_code == 400
    assert "File exceeds maximum rows" in response.json()["detail"]


def test_upload_max_size_limit(stress_client, admin_user):
    set_current_user(admin_user)

    from backend.config import settings

    max_size = settings.MAX_UPLOAD_SIZE

    # Create a file slightly over the limit
    # We can use a large string and encode it
    large_content = "a" * (max_size + 1024)

    response = stress_client.post(
        "/api/v1/files/upload",
        files={"file": ("too_large.csv", large_content, "text/csv")},
    )
    assert response.status_code == 413
    assert "File size exceeds maximum" in response.json()["detail"]
