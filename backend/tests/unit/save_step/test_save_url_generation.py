"""Tests for download URL generation and refresh."""
from unittest.mock import patch, MagicMock

import pyarrow as pa

from backend.execution.steps.save_step import execute_save_step, refresh_download_url


def _make_minio_client():
    client = MagicMock()
    client.presigned_get_object.return_value = "https://minio/presigned-url"
    return client


class TestSaveUrlGeneration:
    def test_download_url_stored_in_result(self):
        table = pa.table({"x": pa.array([1, 2, 3])})
        step = MagicMock()
        step.filename = "output.csv"

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _make_minio_client()
            client.presigned_get_object.return_value = "https://minio/outputs/run-001/output.csv?token=xyz"
            mock_minio.return_value = client

            result = execute_save_step(table, step, run_id="run-001")

            assert result.download_url == "https://minio/outputs/run-001/output.csv?token=xyz"
            assert result.object_name == "outputs/run-001/output.csv"

    def test_refresh_download_url_generates_new_url(self):
        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = _make_minio_client()
            client.presigned_get_object.return_value = "https://minio/refreshed-url"
            mock_minio.return_value = client

            url = refresh_download_url("outputs/run-001/output.csv")
            assert url == "https://minio/refreshed-url"
            assert client.presigned_get_object.called
            call_kwargs = client.presigned_get_object.call_args[1]
            assert call_kwargs["bucket_name"] == "pipelineiq-outputs"
            assert call_kwargs["object_name"] == "outputs/run-001/output.csv"

    def test_size_bytes_is_positive(self):
        table = pa.table({"x": pa.array(range(100))})
        step = MagicMock()
        step.filename = "output.csv"

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            mock_minio.return_value = _make_minio_client()
            result = execute_save_step(table, step, run_id="run-001")
            assert result.size_bytes > 0
