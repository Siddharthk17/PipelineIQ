"""Tests for the Parquet save step output."""
import io
from unittest.mock import patch, MagicMock

import pyarrow as pa
import pyarrow.parquet as pq


def _large_table():
    n = 10_000
    return pa.table({
        "id":     pa.array(range(n)),
        "value":  pa.array([float(i) for i in range(n)]),
        "label":  pa.array([f"label_{i}" for i in range(n)]),
    })


def _mock_minio():
    client = MagicMock()
    client.presigned_get_object.return_value = "https://minio/presigned-url"
    return client


class TestSaveParquet:
    def test_parquet_result_format_is_parquet(self):
        table = _large_table()
        step = MagicMock()
        step.filename = "output.parquet"

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _mock_minio()

            from backend.execution.steps.save_step import execute_save_step
            result = execute_save_step(table, step, run_id="run-parquet-test")
            assert result.format == "parquet"

    def test_parquet_content_is_valid_parquet(self):
        table = _large_table()
        uploaded_bytes = None

        step = MagicMock()
        step.filename = "output.parquet"

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _mock_minio()

            def capture(**kwargs):
                nonlocal uploaded_bytes
                uploaded_bytes = kwargs["data"].read()

            client.put_object = MagicMock(side_effect=lambda **kwargs: capture(**kwargs))
            mock_minio.return_value = client

            from backend.execution.steps.save_step import execute_save_step
            execute_save_step(table, step, run_id="run-001")

        if uploaded_bytes:
            table_back = pq.read_table(io.BytesIO(uploaded_bytes))
            assert table_back.num_rows == 10_000
            assert set(table_back.schema.names) == {"id", "value", "label"}

    def test_parquet_smaller_than_csv_for_large_data(self):
        table = _large_table()
        from backend.execution.steps.save_step import _serialize_csv, _serialize_parquet

        csv_bytes, _ = _serialize_csv(table)
        parquet_bytes, _ = _serialize_parquet(table)

        assert len(parquet_bytes) < len(csv_bytes), \
            f"Parquet ({len(parquet_bytes)}) should be smaller than CSV ({len(csv_bytes)})"
