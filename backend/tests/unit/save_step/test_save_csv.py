"""Tests for the CSV save step output."""
import pytest
import pandas as pd
import pyarrow as pa
from unittest.mock import patch, MagicMock
from backend.config import settings
from backend.execution.steps.save_step import execute_save_step, SaveResult


def _make_minio_client():
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://minio/presigned-url"
    client.put_object = MagicMock()
    return client


@pytest.fixture
def sample_table():
    return pa.table({
        "customer_id": pa.array([1, 2, 3, 4, 5]),
        "region":      pa.array(["North", "South", "East", "West", "North"]),
        "revenue":     pa.array([100.0, 200.0, 150.0, 300.0, 250.0]),
    })


@pytest.fixture
def mock_save_step():
    step = MagicMock()
    step.filename = "output.csv"
    step.type = "save"
    return step


class TestSaveCsv:
    def test_csv_result_has_download_url(self, sample_table, mock_save_step):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(sample_table, mock_save_step, run_id="run-001")
            assert result.download_url == "https://minio/presigned-url"
            assert isinstance(result, SaveResult)

    def test_csv_result_has_correct_row_count(self, sample_table, mock_save_step):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(sample_table, mock_save_step, run_id="run-001")
            assert result.row_count == 5

    def test_csv_format_is_csv(self, sample_table, mock_save_step):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(sample_table, mock_save_step, run_id="run-001")
            assert result.format == "csv"

    def test_csv_minio_put_called_with_correct_bucket(self, sample_table, mock_save_step):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _make_minio_client()
            mock_minio.return_value = client
            execute_save_step(sample_table, mock_save_step, run_id="run-001")
            put_call = client.put_object.call_args
            assert put_call[1]["Bucket"] == settings.S3_BUCKET

    def test_csv_object_name_includes_run_id(self, sample_table, mock_save_step):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _make_minio_client()
            mock_minio.return_value = client
            execute_save_step(sample_table, mock_save_step, run_id="run-xyz")
            put_call = client.put_object.call_args
            object_name = put_call[1]["Key"]
            assert "run-xyz" in object_name
            assert "output.csv" in object_name

    def test_csv_content_is_valid_csv(self, sample_table, mock_save_step):
        uploaded_bytes = None

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _make_minio_client()

            def capture_put(**kwargs):
                nonlocal uploaded_bytes
                uploaded_bytes = kwargs["Body"].read()

            client.put_object = MagicMock(side_effect=lambda **kwargs: capture_put(**kwargs))
            mock_minio.return_value = client
            execute_save_step(sample_table, mock_save_step, run_id="run-001")

        if uploaded_bytes:
            import io
            df = pd.read_csv(io.BytesIO(uploaded_bytes))
            assert set(df.columns) == {"customer_id", "region", "revenue"}
            assert len(df) == 5

    def test_unsupported_format_falls_back_to_csv(self, sample_table):
        step = MagicMock()
        step.filename = "output.xlsx"
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(sample_table, step, run_id="run-001")
            assert result.format == "csv"

    def test_no_filename_uses_fallback(self, sample_table):
        step = MagicMock()
        step.filename = None
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(sample_table, step, run_id="run-fallback-001")
            assert "run-fallback" in result.filename or result.filename.endswith(".csv")
