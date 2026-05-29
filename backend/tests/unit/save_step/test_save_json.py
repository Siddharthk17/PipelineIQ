"""Tests for the JSON save step output."""
import math
from unittest.mock import patch, MagicMock

import orjson
import pyarrow as pa

from backend.execution.steps.save_step import _serialize_json


class TestSaveJson:
    def test_json_serialization_handles_nan(self):
        table = pa.table({"x": pa.array([1.0, float("nan"), 3.0])})
        content, content_type = _serialize_json(table)
        parsed = orjson.loads(content)
        assert parsed[0]["x"] == 1.0
        assert parsed[1]["x"] is None  # NaN -> null
        assert parsed[2]["x"] == 3.0
        assert content_type == "application/json"

    def test_json_serialization_handles_infinity(self):
        table = pa.table({"x": pa.array([1.0, float("inf"), float("-inf"), 3.0])})
        content, _ = _serialize_json(table)
        parsed = orjson.loads(content)
        assert parsed[1]["x"] is None
        assert parsed[2]["x"] is None
        assert parsed[3]["x"] == 3.0

    def test_json_serialization_produces_records_format(self):
        table = pa.table({
            "name": pa.array(["Alice", "Bob"]),
            "score": pa.array([95, 87]),
        })
        content, _ = _serialize_json(table)
        parsed = orjson.loads(content)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["name"] == "Alice"
        assert parsed[0]["score"] == 95

    def test_json_save_step_through_execute(self):
        table = pa.table({"a": pa.array([1, 2, 3]), "b": pa.array(["x", "y", "z"])})
        step = MagicMock()
        step.filename = "output.json"

        with patch("backend.execution.steps.save_step._get_minio_client") as mock_minio:
            client = MagicMock()
            client.generate_presigned_url.return_value = "https://minio/presigned"
            client.put_object = MagicMock()
            mock_minio.return_value = client

            from backend.execution.steps.save_step import execute_save_step
            result = execute_save_step(table, step, run_id="run-json-test")

            assert result.format == "json"
            assert result.download_url == "https://minio/presigned"
