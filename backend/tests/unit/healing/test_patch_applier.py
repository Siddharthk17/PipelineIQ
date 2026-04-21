"""Tests for JSON healing patch application."""

import pytest
import yaml

from backend.ai.healing_prompts import validate_healing_patch
from backend.execution.patch_applier import apply_patch


PIPELINE_YAML = """
pipeline:
  name: revenue_pipeline
  steps:
    - name: load_data
      type: load
      file_id: abc-123
    - name: filter_revenue
      type: filter
      input: load_data
      column: revenue
      operator: greater_than
      value: 1000
    - name: aggregate_by_region
      type: aggregate
      input: filter_revenue
      group_by: [region]
      aggregations:
        - column: revenue
          function: sum
    - name: save_output
      type: save
      input: aggregate_by_region
      filename: output.csv
""".strip()


RENAME_PATCH = {
    "confidence": 0.95,
    "change_description": "Rename revenue to rev_usd",
    "patches": [
        {
            "step_name": "filter_revenue",
            "field": "column",
            "old_value": "revenue",
            "new_value": "rev_usd",
        },
        {
            "step_name": "aggregate_by_region",
            "field": "aggregations",
            "old_value": "revenue",
            "new_value": "rev_usd",
        },
    ],
}


def test_apply_patch_updates_scalar_and_aggregation_fields():
    patched_yaml = apply_patch(PIPELINE_YAML, RENAME_PATCH)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert steps["filter_revenue"]["column"] == "rev_usd"
    assert steps["aggregate_by_region"]["aggregations"][0]["column"] == "rev_usd"


def test_apply_patch_preserves_unrelated_fields():
    patched_yaml = apply_patch(PIPELINE_YAML, RENAME_PATCH)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert steps["load_data"]["file_id"] == "abc-123"
    assert steps["save_output"]["filename"] == "output.csv"


def test_apply_patch_raises_for_unknown_step():
    patch = {
        "confidence": 0.8,
        "change_description": "invalid",
        "patches": [
            {
                "step_name": "missing_step",
                "field": "column",
                "old_value": "a",
                "new_value": "b",
            }
        ],
    }
    with pytest.raises(ValueError, match="missing_step"):
        apply_patch(PIPELINE_YAML, patch)


def test_apply_patch_returns_valid_yaml_for_empty_patch_list():
    patched_yaml = apply_patch(
        PIPELINE_YAML,
        {"confidence": 0.0, "change_description": "no-op", "patches": []},
    )
    parsed = yaml.safe_load(patched_yaml)
    assert parsed["pipeline"]["steps"][1]["column"] == "revenue"


def test_validate_healing_patch_accepts_valid_payload():
    valid, error = validate_healing_patch(RENAME_PATCH)
    assert valid is True
    assert error == ""


def test_validate_healing_patch_rejects_missing_fields():
    valid, error = validate_healing_patch({"confidence": 0.8, "change_description": "bad"})
    assert valid is False
    assert "patches" in error
