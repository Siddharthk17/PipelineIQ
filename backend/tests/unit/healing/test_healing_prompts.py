"""Tests for healing prompt construction and validation."""

from backend.ai.healing_prompts import (
    HEALING_SYSTEM_PROMPT,
    build_healing_prompt,
    validate_healing_patch,
)


def test_build_healing_prompt_includes_required_context():
    prompt = build_healing_prompt(
        broken_yaml="pipeline:\n  name: test\n  steps: []",
        error_type="ColumnNotFoundError",
        error_message="Column 'revenue' not found",
        failed_step_name="filter_revenue",
        old_schema={"revenue": {"semantic_type": "currency"}},
        new_schema={"rev_usd": {"semantic_type": "currency"}},
        schema_diff={
            "removed_columns": ["revenue"],
            "added_columns": ["rev_usd"],
            "renamed_candidates": [
                {
                    "old_name": "revenue",
                    "new_name": "rev_usd",
                    "similarity": 0.91,
                    "type_match": True,
                    "confidence": 0.95,
                }
            ],
        },
    )
    assert "ColumnNotFoundError" in prompt
    assert "filter_revenue" in prompt
    assert "revenue" in prompt
    assert "rev_usd" in prompt
    assert '"confidence"' in prompt
    assert '"patches"' in prompt


def test_prompt_template_enforces_json_only_output():
    assert "Output only a JSON object" in HEALING_SYSTEM_PROMPT
    assert "Do not output YAML" in HEALING_SYSTEM_PROMPT


def test_build_healing_prompt_handles_empty_rename_candidates():
    prompt = build_healing_prompt(
        broken_yaml="pipeline:\n  name: test\n  steps: []",
        error_type="KeyError",
        error_message="revenue",
        failed_step_name="filter",
        old_schema={"revenue": {"semantic_type": "currency"}},
        new_schema={"customer_id": {"semantic_type": "integer_id"}},
        schema_diff={
            "removed_columns": ["revenue"],
            "added_columns": ["customer_id"],
            "renamed_candidates": [],
        },
    )
    assert "none" in prompt


def test_build_healing_prompt_includes_step_name_in_patch_instruction():
    prompt = build_healing_prompt(
        broken_yaml="pipeline:\n  name: test\n  steps: []",
        error_type="ColumnNotFoundError",
        error_message="Column 'city' not found",
        failed_step_name="dedup_step",
        old_schema={},
        new_schema={},
        schema_diff={
            "removed_columns": [],
            "added_columns": [],
            "renamed_candidates": [],
        },
    )
    assert "Patch every step" in prompt


def test_validate_healing_patch_accepts_valid_payload():
    valid = {
        "confidence": 0.95,
        "change_description": "Rename revenue to rev_usd",
        "patches": [
            {
                "step_name": "filter_revenue",
                "field": "column",
                "old_value": "revenue",
                "new_value": "rev_usd",
            }
        ],
    }
    is_valid, error = validate_healing_patch(valid)
    assert is_valid is True
    assert error == ""


def test_validate_healing_patch_rejects_string_input():
    is_valid, error = validate_healing_patch("not a dict")
    assert is_valid is False
    assert "Expected dict" in error


def test_validate_healing_patch_rejects_missing_confidence():
    is_valid, error = validate_healing_patch(
        {"change_description": "Fix", "patches": []})
    assert is_valid is False
    assert "confidence" in error


def test_validate_healing_patch_rejects_confidence_out_of_range():
    is_valid, error = validate_healing_patch(
        {"confidence": 1.5, "change_description": "Fix", "patches": []}
    )
    assert is_valid is False


def test_validate_healing_patch_rejects_missing_patches():
    is_valid, error = validate_healing_patch(
        {"confidence": 0.8, "change_description": "bad"})
    assert is_valid is False
    assert "patches" in error


def test_validate_healing_patch_rejects_patch_item_missing_fields():
    is_valid, error = validate_healing_patch(
        {
            "confidence": 0.9,
            "change_description": "Fix",
            "patches": [{"step_name": "x"}],
        }
    )
    assert is_valid is False
    assert "missing fields" in error


def test_validate_healing_patch_rejects_non_dict_patch_items():
    is_valid, error = validate_healing_patch(
        {
            "confidence": 0.9,
            "change_description": "Fix",
            "patches": ["not a dict"],
        }
    )
    assert is_valid is False
    assert "must be an object" in error
