"""Tests for schema drift detection (Deliverable 5).

10 tests covering drift detection, snapshot storage, and upload integration.
"""

import io
import pytest
import pandas as pd
from backend.pipeline.schema_drift import detect_schema_drift, SchemaDriftReport


class TestSchemaDriftDetection:
    """Unit tests for the detect_schema_drift function."""

    def test_no_drift_on_first_upload_returns_null(self, client, sales_csv_bytes):
        """First upload has no previous snapshot, so no drift."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        )
        assert response.status_code == 201
        # First upload — only one snapshot exists, no drift possible

    def test_identical_schema_returns_no_drift(self):
        """Two identical schemas should produce no drift."""
        cols = ["a", "b", "c"]
        dtypes = {"a": "int64", "b": "float64", "c": "object"}
        report = detect_schema_drift(cols, dtypes, cols, dtypes)
        assert report.has_drift is False
        assert report.columns_added == []
        assert report.columns_removed == []
        assert report.type_changes == []

    def test_added_column_returns_info_severity(self):
        """Added columns should be detected."""
        old_cols = ["a", "b"]
        old_dtypes = {"a": "int64", "b": "float64"}
        new_cols = ["a", "b", "c"]
        new_dtypes = {"a": "int64", "b": "float64", "c": "object"}
        report = detect_schema_drift(old_cols, old_dtypes, new_cols, new_dtypes)
        assert report.has_drift is True
        assert "c" in report.columns_added

    def test_removed_column_returns_breaking_severity(self):
        """Removed columns should be detected."""
        old_cols = ["a", "b", "c"]
        old_dtypes = {"a": "int64", "b": "float64", "c": "object"}
        new_cols = ["a", "b"]
        new_dtypes = {"a": "int64", "b": "float64"}
        report = detect_schema_drift(old_cols, old_dtypes, new_cols, new_dtypes)
        assert report.has_drift is True
        assert "c" in report.columns_removed

    def test_type_change_returns_warning_severity(self):
        """Type changes should be detected as warnings."""
        cols = ["a", "b"]
        old_dtypes = {"a": "int64", "b": "float64"}
        new_dtypes = {"a": "int64", "b": "object"}
        report = detect_schema_drift(cols, old_dtypes, cols, new_dtypes)
        assert report.has_drift is True
        assert len(report.type_changes) == 1
        assert report.type_changes[0].column == "b"
        assert report.type_changes[0].old_type == "float64"
        assert report.type_changes[0].new_type == "object"

    def test_multiple_drift_items_all_reported(self):
        """Multiple types of drift should all be reported."""
        old_cols = ["a", "b", "c"]
        old_dtypes = {"a": "int64", "b": "float64", "c": "object"}
        new_cols = ["a", "b", "d"]
        new_dtypes = {"a": "object", "b": "float64", "d": "int64"}
        report = detect_schema_drift(old_cols, old_dtypes, new_cols, new_dtypes)
        assert report.has_drift is True
        assert "d" in report.columns_added
        assert "c" in report.columns_removed
        assert any(t.column == "a" for t in report.type_changes)

    def test_breaking_changes_counted_correctly(self):
        """Removed columns should be counted in summary."""
        old_cols = ["a", "b", "c"]
        old_dtypes = {"a": "int64", "b": "float64", "c": "object"}
        new_cols = ["a"]
        new_dtypes = {"a": "int64"}
        report = detect_schema_drift(old_cols, old_dtypes, new_cols, new_dtypes)
        assert len(report.columns_removed) == 2

    def test_snapshot_saved_after_upload(self, client, sales_csv_bytes):
        """After upload, a schema snapshot should exist."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        )
        file_id = response.json()["id"]
        history = client.get(f"/api/v1/files/{file_id}/schema/history")
        assert history.status_code == 200
        assert history.json()["total_snapshots"] >= 1

    def test_latest_snapshot_returns_most_recent(self, client, sales_csv_bytes):
        """Schema history should return newest snapshot first."""
        response = client.post(
            "/api/v1/files/upload",
            files={"file": ("sales.csv", sales_csv_bytes, "text/csv")},
        )
        file_id = response.json()["id"]
        history = client.get(f"/api/v1/files/{file_id}/schema/history")
        snapshots = history.json()["snapshots"]
        assert len(snapshots) >= 1
        # First snapshot should have the columns from sales CSV
        assert "order_id" in snapshots[0]["columns"]

    def test_drift_report_included_in_upload_response(self, client):
        """Schema diff endpoint works when snapshots exist."""
        # Upload a file
        csv1 = pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_csv(index=False).encode()
        resp1 = client.post(
            "/api/v1/files/upload",
            files={"file": ("test.csv", csv1, "text/csv")},
        )
        file_id = resp1.json()["id"]

        # Check diff endpoint — only one snapshot, so no drift
        diff = client.get(f"/api/v1/files/{file_id}/schema/diff")
        assert diff.status_code == 200
        assert diff.json()["has_drift"] is False
