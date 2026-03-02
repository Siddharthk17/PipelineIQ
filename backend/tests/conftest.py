"""Shared test fixtures for PipelineIQ test suite.

Provides deterministic sample DataFrames, in-memory SQLite DB sessions,
FastAPI test clients, and pre-configured LineageRecorder instances.
"""

# Standard library
from typing import Generator

# Third-party packages
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Internal modules
from backend.database import Base, get_db
from backend.main import app
from backend.pipeline.lineage import LineageRecorder


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def test_db() -> Generator[Session, None, None]:
    """In-memory SQLite database for each test.

    Creates all tables before the test and drops them after,
    ensuring complete isolation between tests.

    Yields:
        A SQLAlchemy Session bound to the in-memory database.
    """
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(test_db: Session) -> TestClient:
    """FastAPI TestClient with the test database injected.

    Overrides the get_db dependency so all routes use the in-memory
    test database instead of the production database.

    Args:
        test_db: In-memory test database session.

    Returns:
        FastAPI TestClient ready for API testing.
    """

    def override_get_db() -> Generator[Session, None, None]:
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE DATA FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def sample_sales_df() -> pd.DataFrame:
    """Deterministic 20-row sales DataFrame for testing.

    Returns:
        DataFrame with columns: order_id, customer_id, product_id,
        amount, quantity, status, date.
    """
    return pd.DataFrame({
        "order_id": list(range(1001, 1021)),
        "customer_id": [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 6, 7, 8, 9, 10],
        "product_id": [101, 102, 103, 101, 102, 103, 101, 102, 103, 101,
                       102, 103, 101, 102, 103, 101, 102, 103, 101, 102],
        "amount": [
            150.0, 250.0, 350.0, 100.0, 200.0, 300.0, 175.0, 225.0, 275.0, 125.0,
            450.0, 550.0, 150.0, 350.0, 250.0, 175.0, 325.0, 425.0, 50.0, 75.0,
        ],
        "quantity": [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 4, 5, 1, 3, 2, 1, 3, 4, 1, 1],
        "status": [
            "delivered", "delivered", "shipped", "delivered", "cancelled",
            "delivered", "shipped", "delivered", "delivered", "cancelled",
            "delivered", "shipped", "delivered", "delivered", "cancelled",
            "delivered", "shipped", "delivered", "delivered", "shipped",
        ],
        "date": [
            "2024-01-05", "2024-01-06", "2024-01-07", "2024-01-08", "2024-01-09",
            "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-13", "2024-01-14",
            "2024-01-15", "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19",
            "2024-01-20", "2024-01-21", "2024-01-22", "2024-01-23", "2024-01-24",
        ],
    })


@pytest.fixture()
def sample_customers_df() -> pd.DataFrame:
    """Deterministic 10-row customers DataFrame for testing.

    Returns:
        DataFrame with columns: customer_id, name, email, region, tier.
    """
    return pd.DataFrame({
        "customer_id": list(range(1, 11)),
        "name": [
            "Alice Johnson", "Bob Smith", "Charlie Brown", "Diana Ross",
            "Eve Wilson", "Frank Miller", "Grace Lee", "Henry Davis",
            "Ivy Chen", "Jack Taylor",
        ],
        "email": [
            "alice@example.com", "bob@example.com", "charlie@example.com",
            "diana@example.com", "eve@example.com", "frank@example.com",
            "grace@example.com", "henry@example.com", "ivy@example.com",
            "jack@example.com",
        ],
        "region": [
            "North", "South", "East", "West", "North",
            "South", "East", "West", "North", "South",
        ],
        "tier": [
            "gold", "silver", "bronze", "gold", "silver",
            "bronze", "gold", "silver", "bronze", "gold",
        ],
    })


@pytest.fixture()
def sample_products_df() -> pd.DataFrame:
    """Deterministic 5-row products DataFrame for testing.

    Returns:
        DataFrame with columns: product_id, product_name, category, price.
    """
    return pd.DataFrame({
        "product_id": [101, 102, 103, 104, 105],
        "product_name": [
            "Widget A", "Widget B", "Gadget C", "Gadget D", "Tool E",
        ],
        "category": ["widgets", "widgets", "gadgets", "gadgets", "tools"],
        "price": [49.99, 99.99, 149.99, 199.99, 29.99],
    })


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAGE FIXTURE
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def lineage_recorder() -> LineageRecorder:
    """Fresh LineageRecorder instance for each test.

    Returns:
        A new LineageRecorder with an empty graph.
    """
    return LineageRecorder()


# ═══════════════════════════════════════════════════════════════════════════════
# FILE UPLOAD FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture()
def uploaded_sales_file(client: TestClient, tmp_path, sample_sales_df: pd.DataFrame) -> str:
    """Upload sample sales CSV and return the file_id.

    Args:
        client: FastAPI test client.
        tmp_path: Pytest temporary directory.
        sample_sales_df: Sample sales DataFrame.

    Returns:
        The file_id of the uploaded file.
    """
    csv_path = tmp_path / "sales.csv"
    sample_sales_df.to_csv(csv_path, index=False)

    with open(csv_path, "rb") as f:
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("sales.csv", f, "text/csv")},
        )

    assert response.status_code == 201
    return response.json()["id"]
