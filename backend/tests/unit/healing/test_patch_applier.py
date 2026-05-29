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
    - name: deduplicate_ids
      type: deduplicate
      input: aggregate_by_region
      subset: [customer_id]
    - name: rename_cols
      type: rename
      input: deduplicate_ids
      mapping:
        revenue: total_revenue
    - name: sort_by_amount
      type: sort
      input: rename_cols
      by: [total_revenue]
      ascending: [false]
    - name: select_final
      type: select
      input: sort_by_amount
      columns: [customer_id, region, total_revenue]
    - name: save_output
      type: save
      input: select_final
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


def test_apply_patch_updates_group_by_list():
    patch = {
        "confidence": 0.9,
        "change_description": "Rename region to territory",
        "patches": [
            {
                "step_name": "aggregate_by_region",
                "field": "group_by",
                "old_value": "region",
                "new_value": "territory",
            }
        ],
    }
    patched_yaml = apply_patch(PIPELINE_YAML, patch)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert "territory" in steps["aggregate_by_region"]["group_by"]
    assert "region" not in steps["aggregate_by_region"]["group_by"]


def test_apply_patch_updates_sort_by_field():
    patch = {
        "confidence": 0.9,
        "change_description": "Rename sort column",
        "patches": [
            {
                "step_name": "sort_by_amount",
                "field": "by",
                "old_value": "total_revenue",
                "new_value": "adjusted_revenue",
            }
        ],
    }
    patched_yaml = apply_patch(PIPELINE_YAML, patch)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert steps["sort_by_amount"]["by"] == ["adjusted_revenue"]


def test_apply_patch_updates_mapping_field():
    patch = {
        "confidence": 0.9,
        "change_description": "Rename mapping key",
        "patches": [
            {
                "step_name": "rename_cols",
                "field": "mapping",
                "old_value": "revenue",
                "new_value": "rev_usd",
            }
        ],
    }
    patched_yaml = apply_patch(PIPELINE_YAML, patch)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert "rev_usd" in steps["rename_cols"]["mapping"]


def test_apply_patch_updates_multiple_items_in_columns_list():
    pipeline = """
pipeline:
  name: test
  steps:
    - name: select_stuff
      type: select
      input: load_data
      columns: [a, old_col, b]
""".strip()
    patch = {
        "confidence": 0.95,
        "change_description": "Rename old_col to new_col",
        "patches": [
            {
                "step_name": "select_stuff",
                "field": "columns",
                "old_value": "old_col",
                "new_value": "new_col",
            }
        ],
    }
    patched_yaml = apply_patch(pipeline, patch)
    parsed = yaml.safe_load(patched_yaml)
    steps = {step["name"]: step for step in parsed["pipeline"]["steps"]}
    assert "new_col" in steps["select_stuff"]["columns"]
    assert "old_col" not in steps["select_stuff"]["columns"]
    assert "a" in steps["select_stuff"]["columns"]


def test_validate_healing_patch_accepts_valid_payload():
    valid, error = validate_healing_patch(RENAME_PATCH)
    assert valid is True
    assert error == ""


def test_validate_healing_patch_rejects_missing_fields():
    valid, error = validate_healing_patch(
        {"confidence": 0.8, "change_description": "bad"})
    assert valid is False
    assert "patches" in error


def test_apply_patch_raises_on_invalid_yaml():
    with pytest.raises((ValueError, yaml.YAMLError)):
        apply_patch("not: [valid: yaml: {{{", RENAME_PATCH)


def test_apply_patch_output_is_roundtrip_valid():
    patched_yaml = apply_patch(PIPELINE_YAML, RENAME_PATCH)
    parsed = yaml.safe_load(patched_yaml)
    assert "pipeline" in parsed
    assert "steps" in parsed["pipeline"]
    assert isinstance(parsed["pipeline"]["steps"], list)
