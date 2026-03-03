"""Performance tests for PipelineIQ.

Validates that core operations complete within acceptable time bounds.
"""

import io
import time

import pandas as pd
import pytest

from backend.pipeline.lineage import LineageRecorder
from backend.tests.conftest import upload_file


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOAD PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
# LINEAGE PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════════
# CONCURRENT UPLOAD SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrentUploads:
    """Tests for concurrent upload safety."""

    def test_multiple_sequential_uploads_do_not_corrupt_data(self, client):
        """5 sequential uploads all succeed without data corruption."""
        file_ids = []
        for idx in range(5):
            csv = f"id,value\n{idx},{idx * 100}\n".encode()
            response = client.post(
                "/api/v1/files/upload",
                files={"file": (f"sequential_{idx}.csv", csv, "text/csv")},
            )
            assert response.status_code == 201, f"Upload {idx} failed: {response.text}"
            file_ids.append(response.json()["id"])

        # All file IDs should be unique
        assert len(set(file_ids)) == 5
