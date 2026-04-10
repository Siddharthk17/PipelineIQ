"""Tests for pipeline versioning (Deliverable 7).

12 tests covering version creation, diffs, and API endpoints.
"""

import pytest
from backend.pipeline.versioning import diff_pipelines, save_version
from sqlalchemy.exc import IntegrityError


YAML_V1 = """pipeline:
  name: test_pipeline
  steps:
    - name: load_data
      type: load
      file_id: "abc-123"
    - name: filter_rows
      type: filter
      input: load_data
      column: status
      operator: equals
      value: active
    - name: save_output
      type: save
      input: filter_rows
      filename: output.csv
"""

YAML_V2 = """pipeline:
  name: test_pipeline
  steps:
    - name: load_data
      type: load
      file_id: "abc-123"
    - name: filter_rows
      type: filter
      input: load_data
      column: status
      operator: equals
      value: delivered
    - name: sort_rows
      type: sort
      input: filter_rows
      by: amount
      order: desc
    - name: save_output
      type: save
      input: sort_rows
      filename: output.csv
"""


class TestVersionCreation:
    """Tests for version saving logic."""

    def test_first_run_creates_version_1(self, test_db):
        version = save_version("my_pipeline", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        assert version.version_number == 1
        assert version.change_summary == "Initial version"

    def test_second_run_creates_version_2(self, test_db):
        save_version("my_pipeline", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        v2 = save_version("my_pipeline", YAML_V2, "b0000000-0000-0000-0000-000000000002", test_db)
        assert v2.version_number == 2
        assert v2.change_summary != "Initial version"

    def test_independent_version_sequences_per_pipeline_name(self, test_db):
        v1a = save_version("pipeline_a", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        v1b = save_version("pipeline_b", YAML_V1, "b0000000-0000-0000-0000-000000000002", test_db)
        assert v1a.version_number == 1
        assert v1b.version_number == 1

    def test_save_version_retries_after_integrity_error(self, test_db, monkeypatch):
        save_version(
            "retry_pipeline",
            YAML_V1,
            "a0000000-0000-0000-0000-000000000001",
            test_db,
        )

        original_commit = test_db.commit
        state = {"calls": 0}

        def flaky_commit():
            state["calls"] += 1
            if state["calls"] == 1:
                raise IntegrityError(
                    "INSERT INTO pipeline_versions ...",
                    params={},
                    orig=Exception("duplicate key value violates unique constraint"),
                )
            return original_commit()

        monkeypatch.setattr(test_db, "commit", flaky_commit)

        v2 = save_version(
            "retry_pipeline",
            YAML_V2,
            "b0000000-0000-0000-0000-000000000002",
            test_db,
        )
        assert v2.version_number == 2
        assert state["calls"] == 2


class TestPipelineDiff:
    """Tests for diff computation between versions."""

    def test_diff_detects_added_step(self):
        diff = diff_pipelines(YAML_V1, YAML_V2, 1, 2)
        assert "sort_rows" in diff.steps_added

    def test_diff_detects_removed_step(self):
        diff = diff_pipelines(YAML_V2, YAML_V1, 2, 3)
        assert "sort_rows" in diff.steps_removed

    def test_diff_detects_modified_field(self):
        diff = diff_pipelines(YAML_V1, YAML_V2, 1, 2)
        modified_names = [s.step_name for s in diff.steps_modified]
        assert "filter_rows" in modified_names or "save_output" in modified_names

    def test_diff_no_changes_has_changes_false(self):
        diff = diff_pipelines(YAML_V1, YAML_V1, 1, 2)
        assert diff.has_changes is False

    def test_diff_generates_unified_diff_string(self):
        diff = diff_pipelines(YAML_V1, YAML_V2, 1, 2)
        assert "---" in diff.unified_diff or "+++" in diff.unified_diff

    def test_change_summary_human_readable(self):
        diff = diff_pipelines(YAML_V1, YAML_V2, 1, 2)
        assert "added" in diff.change_summary or "modified" in diff.change_summary


class TestVersioningAPI:
    """Tests for versioning API endpoints."""

    def test_restore_returns_correct_yaml(self, test_db):
        save_version("my_pipeline", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        save_version("my_pipeline", YAML_V2, "b0000000-0000-0000-0000-000000000002", test_db)
        # Restore v1 — should create v3 with v1's config
        v3 = save_version("my_pipeline", YAML_V1, None, test_db)
        assert v3.version_number == 3
        assert v3.yaml_config == YAML_V1

    def test_list_versions_newest_first(self, client, test_db):
        save_version("my_pipeline", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        save_version("my_pipeline", YAML_V2, "b0000000-0000-0000-0000-000000000002", test_db)
        response = client.get("/api/v1/versions/my_pipeline")
        assert response.status_code == 200
        data = response.json()
        assert data["total_versions"] == 2
        assert data["versions"][0]["version_number"] == 2

    def test_diff_endpoint_returns_pipeline_diff(self, client, test_db):
        save_version("my_pipeline", YAML_V1, "a0000000-0000-0000-0000-000000000001", test_db)
        save_version("my_pipeline", YAML_V2, "b0000000-0000-0000-0000-000000000002", test_db)
        response = client.get("/api/v1/versions/my_pipeline/diff/1/2")
        assert response.status_code == 200
        data = response.json()
        assert data["has_changes"] is True
