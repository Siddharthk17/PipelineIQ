import pytest
from typing import Generator
from fastapi import Depends
from sqlalchemy.orm import Session
from backend.main import app
from backend.auth import get_current_user
from backend.models import (
    User,
    PipelinePermission,
    PermissionLevel,
    PipelineRun,
    PipelineStatus,
)
import uuid as _uuid
from backend.database import get_db
from backend.dependencies import get_read_db, get_write_db
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch


def set_current_user(user: User):
    """Override get_current_user dependency to return the specified user."""
    app.dependency_overrides[get_current_user] = lambda: user


from unittest.mock import MagicMock, patch


@pytest.fixture()
def rbac_client(test_db: Session, tmp_path) -> TestClient:
    """TestClient with base infrastructure, but auth managed per test."""

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db
    app.dependency_overrides[get_write_db] = override_get_db

    # Patch settings and celery tasks for the duration of the test
    settings_patcher = patch("backend.api.files.settings")
    mock_settings = settings_patcher.start()

    task_patcher = patch("backend.api.pipelines.execute_pipeline_task")
    mock_task = task_patcher.start()
    mock_task.delay = MagicMock(return_value=MagicMock(id="mock-task-id"))

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    mock_settings.UPLOAD_DIR = upload_dir
    mock_settings.ALLOWED_EXTENSIONS = {".csv", ".json"}
    mock_settings.MAX_UPLOAD_SIZE = 50 * 1024 * 1024
    mock_settings.MAX_ROWS_PER_FILE = 1000000

    client = TestClient(app)

    yield client

    # Stop patchers
    settings_patcher.stop()
    task_patcher.stop()
    app.dependency_overrides.clear()


@pytest.fixture()
def users(test_db):
    """Create a set of users with different roles."""
    admin = User(
        id=_uuid.uuid4(),
        email="admin@test.com",
        username="admin",
        hashed_password="hashed_password",
        role="admin",
        is_active=True,
    )
    viewer = User(
        id=_uuid.uuid4(),
        email="viewer@test.com",
        username="viewer",
        hashed_password="hashed_password",
        role="viewer",
        is_active=True,
    )
    runner = User(
        id=_uuid.uuid4(),
        email="runner@test.com",
        username="runner",
        hashed_password="hashed_password",
        role="viewer",
        is_active=True,
    )
    owner = User(
        id=_uuid.uuid4(),
        email="owner@test.com",
        username="owner",
        hashed_password="hashed_password",
        role="viewer",
        is_active=True,
    )

    test_db.add_all([admin, viewer, runner, owner])
    test_db.commit()
    return {"admin": admin, "viewer": viewer, "runner": runner, "owner": owner}


@pytest.fixture()
def sample_pipeline(test_db, users):
    """Setup a pipeline with permissions."""
    pipeline_name = "rbac_test_pipeline"

    # Owner permission
    p_owner = PipelinePermission(
        pipeline_name=pipeline_name,
        user_id=users["owner"].id,
        permission_level=PermissionLevel.OWNER,
    )
    # Runner permission
    p_runner = PipelinePermission(
        pipeline_name=pipeline_name,
        user_id=users["runner"].id,
        permission_level=PermissionLevel.RUNNER,
    )
    # Viewer permission
    p_viewer = PipelinePermission(
        pipeline_name=pipeline_name,
        user_id=users["viewer"].id,
        permission_level=PermissionLevel.VIEWER,
    )

    test_db.add_all([p_owner, p_runner, p_viewer])
    test_db.commit()
    return pipeline_name


@pytest.fixture()
def sample_run(test_db, sample_pipeline):
    """Create a completed pipeline run."""
    run = PipelineRun(
        id=_uuid.uuid4(),
        name=sample_pipeline,
        status=PipelineStatus.COMPLETED,
        yaml_config="pipeline: {name: rbac_test_pipeline, steps: []}",
        user_id=None,  # simplify
    )
    test_db.add(run)
    test_db.commit()
    return run


def test_run_pipeline_rbac(rbac_client, users, sample_pipeline, test_db):
    # Create a dummy file to satisfy validation
    file_id = str(_uuid.uuid4())
    from backend.models import UploadedFile

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
            user_id=users["owner"].id,
        )
    )
    test_db.commit()
    # Use the id of the created file
    file_id = str(test_db.query(UploadedFile).first().id)

    yaml_config = f"pipeline: {{name: {sample_pipeline}, steps: [{{name: load_step, type: load, file_id: {file_id}}}]}}"
    payload = {"yaml_config": yaml_config, "name": sample_pipeline}

    # Admin can run
    set_current_user(users["admin"])
    assert rbac_client.post("/api/v1/pipelines/run", json=payload).status_code == 202

    # Owner can run
    set_current_user(users["owner"])
    assert rbac_client.post("/api/v1/pipelines/run", json=payload).status_code == 202

    # Runner can run
    set_current_user(users["runner"])
    assert rbac_client.post("/api/v1/pipelines/run", json=payload).status_code == 202

    # Viewer cannot run
    set_current_user(users["viewer"])
    assert rbac_client.post("/api/v1/pipelines/run", json=payload).status_code == 403


def test_cancel_pipeline_rbac(rbac_client, users, test_db, sample_pipeline):
    # Create a RUNNING pipeline run
    import uuid as _uuid
    from backend.models import PipelineRun, PipelineStatus

    run = PipelineRun(
        id=_uuid.uuid4(),
        name=sample_pipeline,
        status=PipelineStatus.RUNNING,
        yaml_config="pipeline: {name: rbac_test_pipeline, steps: []}",
        user_id=users["owner"].id,
    )
    test_db.add(run)
    test_db.commit()
    run_id = str(run.id)

    # Admin can cancel
    set_current_user(users["admin"])
    assert rbac_client.post(f"/api/v1/pipelines/{run_id}/cancel").status_code == 200

    # Reset status to RUNNING for next tests
    run.status = PipelineStatus.RUNNING
    test_db.commit()

    # Owner can cancel
    set_current_user(users["owner"])
    assert rbac_client.post(f"/api/v1/pipelines/{run_id}/cancel").status_code == 200

    run.status = PipelineStatus.RUNNING
    test_db.commit()

    # Runner can cancel
    set_current_user(users["runner"])
    assert rbac_client.post(f"/api/v1/pipelines/{run_id}/cancel").status_code == 200

    run.status = PipelineStatus.RUNNING
    test_db.commit()

    # Viewer cannot cancel
    set_current_user(users["viewer"])
    assert rbac_client.post(f"/api/v1/pipelines/{run_id}/cancel").status_code == 403


def test_export_pipeline_rbac(rbac_client, users, sample_run):
    run_id = str(sample_run.id)

    # Admin can export
    set_current_user(users["admin"])
    # We might get 404 if no file exists, but we check for 403 vs 200/404
    resp = rbac_client.get(f"/api/v1/pipelines/{run_id}/export")
    assert resp.status_code != 403

    # Owner can export
    set_current_user(users["owner"])
    resp = rbac_client.get(f"/api/v1/pipelines/{run_id}/export")
    assert resp.status_code != 403

    # Runner can export
    set_current_user(users["runner"])
    resp = rbac_client.get(f"/api/v1/pipelines/{run_id}/export")
    assert resp.status_code != 403

    # Viewer cannot export
    set_current_user(users["viewer"])
    resp = rbac_client.get(f"/api/v1/pipelines/{run_id}/export")
    assert resp.status_code == 403


def test_permission_management_rbac(rbac_client, users, sample_pipeline):
    pipeline_name = sample_pipeline
    target_user_id = str(users["viewer"].id)
    payload = {"user_id": target_user_id, "permission_level": "runner"}

    # Admin can grant
    set_current_user(users["admin"])
    assert (
        rbac_client.post(
            f"/api/v1/pipelines/{pipeline_name}/permissions", json=payload
        ).status_code
        == 201
    )

    # Owner can grant
    set_current_user(users["owner"])
    assert (
        rbac_client.post(
            f"/api/v1/pipelines/{pipeline_name}/permissions", json=payload
        ).status_code
        == 201
    )

    # Runner cannot grant
    set_current_user(users["runner"])
    assert (
        rbac_client.post(
            f"/api/v1/pipelines/{pipeline_name}/permissions", json=payload
        ).status_code
        == 403
    )

    # Viewer cannot grant
    set_current_user(users["viewer"])
    assert (
        rbac_client.post(
            f"/api/v1/pipelines/{pipeline_name}/permissions", json=payload
        ).status_code
        == 403
    )


def test_list_permissions_rbac(rbac_client, users, sample_pipeline):
    pipeline_name = sample_pipeline

    # Admin can list
    set_current_user(users["admin"])
    assert (
        rbac_client.get(f"/api/v1/pipelines/{pipeline_name}/permissions").status_code
        == 200
    )

    # Owner can list
    set_current_user(users["owner"])
    assert (
        rbac_client.get(f"/api/v1/pipelines/{pipeline_name}/permissions").status_code
        == 200
    )

    # Runner cannot list
    set_current_user(users["runner"])
    assert (
        rbac_client.get(f"/api/v1/pipelines/{pipeline_name}/permissions").status_code
        == 403
    )

    # Viewer cannot list
    set_current_user(users["viewer"])
    assert (
        rbac_client.get(f"/api/v1/pipelines/{pipeline_name}/permissions").status_code
        == 403
    )
