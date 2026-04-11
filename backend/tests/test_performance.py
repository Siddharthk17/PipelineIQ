"""Performance tests for PipelineIQ.

Validates that core operations complete within acceptable time bounds.
"""

import io
import time
import uuid as _uuid
import numpy as np

import pandas as pd
import pytest

from backend.auth import get_current_user
from backend.dependencies import get_db, get_read_db, get_write_db
from backend.main import app
from backend.models import LineageGraph, PipelineRun, PipelineStatus, User
from backend.pipeline.lineage import LineageRecorder
from backend.pipeline.parser import StepType, FilterOperator, FilterStepConfig
from backend.tests.conftest import upload_file
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# New imports for execution performance
import pyarrow as pa
from backend.execution.smart_executor import SmartExecutor
from backend.execution.duckdb_executor import DuckDBExecutor
from backend.pipeline.steps import StepExecutor
from backend.execution.arrow_bus import get_arrow_bus


class TestUploadPerformance:
    """Performance tests for file upload."""

    def _generate_csv(self, num_rows: int) -> bytes:
        """Generate a CSV with the given number of rows."""
        df = pd.DataFrame(
            {
                "order_id": range(num_rows),
                "customer_id": [f"C{i:06d}" for i in range(num_rows)],
                "amount": [float(i * 10.5) for i in range(num_rows)],
                "status": ["delivered"] * num_rows,
                "region": ["US"] * num_rows,
            }
        )
        buf = io.BytesIO()
        df.to_csv(buf, index=False)
        return buf.getvalue()

    def test_upload_1k_rows_under_2_seconds(self, client):
        """1,000-row CSV uploads and parses in under 2 seconds."""
        csv_data = self._generate_csv(1_000)
        start = time.time()
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("perf_1k.csv", csv_data, "text/csv")},
        )
        duration = time.time() - start
        assert response.status_code == 201
        assert duration < 2.0, f"Upload took {duration:.2f}s (limit: 2s)"

    def test_upload_100k_rows_under_10_seconds(self, client):
        """100,000-row CSV uploads and parses in under 10 seconds."""
        csv_data = self._generate_csv(100_000)
        start = time.time()
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("perf_100k.csv", csv_data, "text/csv")},
        )
        duration = time.time() - start
        assert response.status_code == 201
        assert duration < 10.0, f"Upload took {duration:.2f}s (limit: 10s)"


class TestLineagePerformance:
    """Performance tests for lineage graph operations."""

    def test_lineage_graph_50_columns_under_500ms(self):
        """Lineage graph generation with 50 columns completes in under 500ms."""
        recorder = LineageRecorder()
        columns = [f"col_{i}" for i in range(50)]

        start = time.time()
        recorder.record_load("f1", "big.csv", "load_step", columns, {})
        recorder.record_passthrough("filter_step", "filter", "load_step", columns)
        recorder.record_passthrough("sort_step", "sort", "filter_step", columns)

        for col in columns[:5]:
            recorder.get_column_ancestry("sort_step", col)
            recorder.get_impact_analysis("load_step", col)

        recorder.to_react_flow_format()
        duration = time.time() - start

        assert duration < 0.5, f"Lineage took {duration:.2f}s (limit: 0.5s)"

    def test_lineage_serialize_under_100ms(self):
        """Serializing a lineage graph takes under 100ms."""
        recorder = LineageRecorder()
        columns = [f"col_{i}" for i in range(30)]
        recorder.record_load("f1", "data.csv", "load", columns, {})
        recorder.record_passthrough("filter", "filter", "load", columns)

        start = time.time()
        data = recorder.serialize()
        duration = time.time() - start

        assert duration < 0.1, f"Serialize took {duration:.2f}s (limit: 0.1s)"
        assert "graph_data" in data


def set_current_user(user: User):
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture()
def perf_client(test_db: Session, tmp_path):
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_read_db] = override_get_db
    app.dependency_overrides[get_write_db] = override_get_db

    from backend.api.files import settings as file_settings

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    file_settings.UPLOAD_DIR = upload_dir

    return TestClient(app)


@pytest.fixture()
def admin_user(test_db: Session):
    user = User(
        id=_uuid.uuid4(),
        email="perf@test.com",
        username="perf_admin",
        hashed_password="hashed_password",
        role="admin",
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    return user


class TestApiLatency:
    """Latency benchmarks for core API endpoints."""

    def test_stats_latency(self, perf_client, admin_user):
        set_current_user(admin_user)
        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            perf_client.get("/api/v1/pipelines/stats")
            latencies.append(time.perf_counter() - start)

        avg_latency = sum(latencies) / len(latencies)
        assert avg_latency < 0.1, f"Average /stats latency too high: {avg_latency:.4f}s"


class TestLineageRetrieval:
    """Performance of retrieving pre-computed lineage graphs."""

    def test_complex_graph_retrieval(self, perf_client, admin_user, test_db):
        set_current_user(admin_user)
        run_id = _uuid.uuid4()
        run = PipelineRun(
            id=run_id,
            name="complex_pipeline",
            status=PipelineStatus.COMPLETED,
            yaml_config="pipeline: {name: complex_pipeline, steps: []}",
        )
        test_db.add(run)

        # Simulate a large pre-computed graph (100 steps, 10 columns each)
        nodes = []
        edges = []
        nodes.append(
            {
                "id": "file::src",
                "type": "sourceFile",
                "data": {"label": "src.csv"},
                "position": {"x": 0, "y": 0},
            }
        )
        for i in range(100):
            step_name = f"step_{i}"
            nodes.append(
                {
                    "id": f"step::{step_name}",
                    "type": "stepNode",
                    "data": {"label": step_name},
                    "position": {"x": i * 300, "y": 0},
                }
            )
            for j in range(10):
                col_id = f"col::{step_name}::{f'col_{j}'}"
                nodes.append(
                    {
                        "id": col_id,
                        "type": "columnNode",
                        "data": {"label": f"col_{j}"},
                        "position": {"x": i * 300, "y": j * 80},
                    }
                )
                if i == 0:
                    edges.append(
                        {
                            "id": f"file::src-{col_id}",
                            "source": "file::src",
                            "target": col_id,
                        }
                    )
                else:
                    edges.append(
                        {
                            "id": f"col::step_{i - 1}::{f'col_{j}'}-{col_id}",
                            "source": f"col::step_{i - 1}::{f'col_{j}'}",
                            "target": col_id,
                        }
                    )

        lineage = LineageGraph(
            pipeline_run_id=run_id,
            graph_data={"nodes": nodes, "edges": edges},
            react_flow_data={"nodes": nodes, "edges": edges},
        )
        test_db.add(lineage)
        test_db.commit()

        start = time.perf_counter()
        response = perf_client.get(f"/api/v1/lineage/{run_id}")
        duration = time.perf_counter() - start

        assert response.status_code == 200
        assert duration < 0.5, (
            f"Retrieving large lineage graph took too long: {duration:.4f}s"
        )


class TestExecutionPerformance:
    """Execution performance benchmarks for the SmartExecutor."""

    def test_small_pipeline_execution_latency(self):
        """A simple pipeline on 10k rows should execute in under 200ms."""
        bus = get_arrow_bus()
        bus.clear_all()
        executor = SmartExecutor(StepExecutor(), DuckDBExecutor())
        recorder = LineageRecorder()

        # Generate 10k rows
        df = pd.DataFrame(
            {
                "col_0": np.random.randint(0, 100, 10_000),
                "category": np.random.choice(["A", "B"], 10_000),
            }
        )
        table = pa.Table.from_pandas(df)
        bus.put("load", table, run_id="perf_test")

        filter_cfg = FilterStepConfig(
            name="filter",
            step_type=StepType.FILTER,
            input="load",
            column="col_0",
            operator=FilterOperator.GREATER_THAN,
            value=50,
        )

        start = time.perf_counter()
        # We use la_registry from bus for simulation
        registry = {"load": bus.get("load")}
        executor.execute_step(filter_cfg, registry, recorder)
        duration = time.perf_counter() - start

        assert duration < 0.2, f"Execution took too long: {duration:.4f}s"
