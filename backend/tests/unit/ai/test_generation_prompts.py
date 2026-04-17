"""Tests for AI prompt structure and YAML cleaning behavior."""

from backend.ai.generation import _clean_yaml_response
from backend.ai.prompts import (
    GENERATION_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    SELF_FIX_PROMPT,
    STEP_TYPE_REFERENCE,
)


class TestGenerationPrompt:
    def test_step_type_reference_contains_supported_types(self):
        required_types = [
            "load",
            "filter",
            "join",
            "aggregate",
            "sort",
            "select",
            "rename",
            "validate",
            "save",
            "pivot",
            "unpivot",
            "deduplicate",
            "fill_nulls",
            "sample",
            "sql",
        ]
        for step_type in required_types:
            assert step_type in STEP_TYPE_REFERENCE

    def test_generation_prompt_has_required_template_markers(self):
        for marker in ("{step_type_reference}", "{file_schemas_section}", "{user_request}"):
            assert marker in GENERATION_SYSTEM_PROMPT

    def test_generation_prompt_demands_yaml_only(self):
        prompt_lower = GENERATION_SYSTEM_PROMPT.lower()
        assert "only valid yaml" in prompt_lower
        assert "no markdown code fences" in prompt_lower

    def test_step_reference_documents_join_and_input(self):
        assert "input:" in STEP_TYPE_REFERENCE
        assert "left:" in STEP_TYPE_REFERENCE
        assert "right:" in STEP_TYPE_REFERENCE

    def test_step_reference_documents_sql_placeholder(self):
        assert "{input}" in STEP_TYPE_REFERENCE


class TestRepairAndSelfFixPrompts:
    def test_repair_prompt_has_required_markers(self):
        for marker in (
            "{original_yaml}",
            "{failed_step}",
            "{error_type}",
            "{error_message}",
            "{file_schemas_section}",
        ):
            assert marker in REPAIR_SYSTEM_PROMPT

    def test_repair_prompt_requires_yaml_only(self):
        assert "only the corrected yaml" in REPAIR_SYSTEM_PROMPT.lower()

    def test_self_fix_prompt_contains_context_markers(self):
        assert "{validation_error}" in SELF_FIX_PROMPT
        assert "{invalid_yaml}" in SELF_FIX_PROMPT
        assert "only the corrected yaml" in SELF_FIX_PROMPT.lower()


class TestCleanYamlResponse:
    def test_strips_markdown_code_fence(self):
        raw = "```yaml\npipeline:\n  name: test\n```"
        result = _clean_yaml_response(raw)
        assert not result.startswith("```")
        assert "pipeline:" in result

    def test_strips_preamble_before_pipeline(self):
        raw = "Here is the YAML:\n\npipeline:\n  name: test"
        result = _clean_yaml_response(raw)
        assert result.startswith("pipeline:")

    def test_clean_yaml_stays_unchanged(self):
        clean = "pipeline:\n  name: test\n  steps: []"
        assert _clean_yaml_response(clean) == clean

