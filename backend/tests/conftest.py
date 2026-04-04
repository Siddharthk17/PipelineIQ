"""Shared test fixtures for PipelineIQ test suite.

Provides deterministic sample DataFrames, in-memory SQLite DB sessions,
FastAPI test clients, and pre-configured LineageRecorder instances.
"""

import io
import os
from typing import Generator
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_db, get_read_db, get_write_db
from backend.main import app
import backend.models  # noqa: F401 — ensure ORM models are registered with Base.metadata
from backend.pipeline.lineage import LineageRecorder
from backend.utils.rate_limiter import limiter


TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset rate limiter storage between ALL tests."""
    limiter.reset()
    yield


@pytest.fixture()
def test_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def test_db(test_engine) -> Generator[Session, None, None]:
    """In-memory SQLite database for each test."""
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(test_db: Session, tmp_path) -> TestClient:
    """FastAPI TestClient with the test database injected.

    Auth dependencies are overridden with a mock admin user so existing
    tests that hit protected endpoints continue to work without tokens.
    """

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db
    app.dependency_overrides[get_write_db] = override_get_db

    # Override auth dependencies with a mock admin user
    from backend.auth import get_current_user, get_current_admin, get_optional_user
    from backend.models import User
    import uuid as _uuid

    mock_user = User(
        id=_uuid.uuid4(),
        email="testadmin@test.com",
        username="testadmin",
        hashed_password="hashed",
        role="admin",
        is_active=True,
    )

    async def override_current_user():
        return mock_user

    async def override_current_admin():
        return mock_user

    def override_optional_user():
        return mock_user

    app.dependency_overrides[get_current_user] = override_current_user
    app.dependency_overrides[get_current_admin] = override_current_admin
    app.dependency_overrides[get_optional_user] = override_optional_user

    # Patch upload dir to use tmp_path
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    with (
        patch("backend.api.files.settings") as mock_settings,
        patch("backend.api.pipelines.execute_pipeline_task") as mock_task,
    ):
        mock_settings.UPLOAD_DIR = upload_dir
        mock_settings.ALLOWED_EXTENSIONS = {".csv", ".json"}
        mock_settings.MAX_UPLOAD_SIZE = 50 * 1024 * 1024
        mock_settings.MAX_ROWS_PER_FILE = 1000000
        mock_task.delay = MagicMock(return_value=MagicMock(id="mock-task-id"))
        test_client = TestClient(app)
        test_client._mock_task = mock_task
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def auth_client(test_db: Session, tmp_path) -> TestClient:
    """TestClient WITHOUT auth overrides — for testing real auth flows."""

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db
    app.dependency_overrides[get_write_db] = override_get_db

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    with (
        patch("backend.api.files.settings") as mock_settings,
        patch("backend.api.pipelines.execute_pipeline_task") as mock_task,
    ):
        mock_settings.UPLOAD_DIR = upload_dir
        mock_settings.ALLOWED_EXTENSIONS = {".csv", ".json"}
        mock_settings.MAX_UPLOAD_SIZE = 50 * 1024 * 1024
        mock_settings.MAX_ROWS_PER_FILE = 1000000
        mock_task.delay = MagicMock(return_value=MagicMock(id="mock-task-id"))
        test_client = TestClient(app)
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture()
def sample_sales_df() -> pd.DataFrame:
    """Deterministic 20-row sales DataFrame for testing.

    8 delivered, 6 cancelled, 6 pending.
    Columns: order_id, customer_id, amount, status, region, date.
    """
    return pd.DataFrame(
        {
            "order_id": list(range(1001, 1021)),
            "customer_id": [
                "C001",
                "C002",
                "C003",
                "C004",
                "C005",
                "C001",
                "C002",
                "C003",
                "C004",
                "C005",
                "C006",
                "C007",
                "C008",
                "C009",
                "C010",
                "C006",
                "C007",
                "C008",
                "C009",
                "C010",
            ],
            "amount": [
                150.0,
                250.0,
                350.0,
                100.0,
                200.0,
                300.0,
                175.0,
                225.0,
                275.0,
                125.0,
                450.0,
                550.0,
                150.0,
                350.0,
                250.0,
                175.0,
                325.0,
                425.0,
                50.0,
                75.0,
            ],
            "status": [
                "delivered",
                "delivered",
                "cancelled",
                "delivered",
                "pending",
                "delivered",
                "cancelled",
                "pending",
                "delivered",
                "cancelled",
                "pending",
                "delivered",
                "cancelled",
                "pending",
                "delivered",
                "cancelled",
                "pending",
                "delivered",
                "cancelled",
                "pending",
            ],
            "region": [
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
                "East",
                "West",
            ],
            "date": [
                "2024-01-05",
                "2024-01-06",
                "2024-01-07",
                "2024-01-08",
                "2024-01-09",
                "2024-01-10",
                "2024-01-11",
                "2024-01-12",
                "2024-01-13",
                "2024-01-14",
                "2024-01-15",
                "2024-01-16",
                "2024-01-17",
                "2024-01-18",
                "2024-01-19",
                "2024-01-20",
                "2024-01-21",
                "2024-01-22",
                "2024-01-23",
                "2024-01-24",
            ],
        }
    )


@pytest.fixture()
def sample_customers_df() -> pd.DataFrame:
    """Deterministic 10-row customers DataFrame for testing."""
    return pd.DataFrame(
        {
            "customer_id": [
                "C001",
                "C002",
                "C003",
                "C004",
                "C005",
                "C006",
                "C007",
                "C008",
                "C009",
                "C010",
            ],
            "customer_name": [
                "Alice Johnson",
                "Bob Smith",
                "Charlie Brown",
                "Diana Ross",
                "Eve Wilson",
                "Frank Miller",
                "Grace Lee",
                "Henry Davis",
                "Ivy Chen",
                "Jack Taylor",
            ],
            "email": [
                "alice@example.com",
                "bob@example.com",
                "charlie@example.com",
                "diana@example.com",
                "eve@example.com",
                "frank@example.com",
                "grace@example.com",
                "henry@example.com",
                "ivy@example.com",
                "jack@example.com",
            ],
            "region": [
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
                "East",
                "West",
                "North",
                "South",
            ],
            "tier": [
                "gold",
                "silver",
                "bronze",
                "gold",
                "silver",
                "bronze",
                "gold",
                "silver",
                "bronze",
                "gold",
            ],
        }
    )


@pytest.fixture()
def sample_products_df() -> pd.DataFrame:
    """Deterministic 5-row products DataFrame for testing."""
    return pd.DataFrame(
        {
            "product_id": [101, 102, 103, 104, 105],
            "product_name": [
                "Widget A",
                "Widget B",
                "Gadget C",
                "Gadget D",
                "Tool E",
            ],
            "category": ["widgets", "widgets", "gadgets", "gadgets", "tools"],
            "price": [49.99, 99.99, 149.99, 199.99, 29.99],
        }
    )


@pytest.fixture()
def sales_csv_bytes(sample_sales_df: pd.DataFrame) -> bytes:
    """Sales DataFrame serialized as CSV bytes."""
    return sample_sales_df.to_csv(index=False).encode()


@pytest.fixture()
def customers_csv_bytes(sample_customers_df: pd.DataFrame) -> bytes:
    """Customers DataFrame serialized as CSV bytes."""
    return sample_customers_df.to_csv(index=False).encode()


@pytest.fixture()
def sample_json_bytes() -> bytes:
    """Simple JSON array as bytes for upload testing."""
    return b'[{"id":1,"name":"Alice","value":100},{"id":2,"name":"Bob","value":200}]'


@pytest.fixture()
def lineage_recorder() -> LineageRecorder:
    """Fresh LineageRecorder instance for each test."""
    return LineageRecorder()


def upload_file(
    client: TestClient, csv_bytes: bytes, filename: str = "test.csv"
) -> str:
    """Upload a CSV file and return its file_id."""
    response = client.post(
        "/api/v1/files/upload",
        files={"file": (filename, csv_bytes, "text/csv")},
    )
    assert response.status_code == 201, f"Upload failed: {response.json()}"
    return response.json()["id"]


def build_simple_pipeline_yaml(file_id: str) -> str:
    """Build a minimal valid pipeline YAML for testing."""
    return f"""pipeline:
  name: test_pipeline
  steps:
    - name: load_sales
      type: load
      file_id: "{file_id}"
    - name: filter_delivered
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: "delivered"
    - name: save_output
      type: save
      input: filter_delivered
      filename: output.csv
"""


@pytest.fixture()
def uploaded_sales_file(client: TestClient, sales_csv_bytes: bytes) -> str:
    """Upload sample sales CSV and return the file_id."""
    return upload_file(client, sales_csv_bytes, "sales.csv")
