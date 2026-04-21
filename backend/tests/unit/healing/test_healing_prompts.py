"""Tests for healing prompt construction."""

from backend.ai.healing_prompts import HEALING_SYSTEM_PROMPT, build_healing_prompt


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
