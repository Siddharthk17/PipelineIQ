import pytest

from backend.ai.generation import repair_pipeline_from_error


@pytest.mark.asyncio
async def test_repair_pipeline_handles_quota_placeholder_without_leaking_text(monkeypatch, test_db):
    original_yaml = """
pipeline:
  name: test
  steps:
    - name: load_data
      type: load
      file_id: "00000000-0000-0000-0000-000000000000"
""".strip()

    quota_placeholder = """
[AUTO-GENERATED due to free-tier quota exhausted]
Prompt summary: You are a PipelineIQ pipeline repair agent.

CRITICAL: Output ONLY the corrected YAML. Nothing else.
- No explanation
- No code fences (no ```)
- No "Here is the fix:" or similar preamble
- Start directly with: pipeline:
- Analyzed input structure
- Would apply transformations if Gemini API was available
- Placeholder output (api unavailable)
""".strip()

    async def _fake_call_gemini_async(*_args, **_kwargs):
        return quota_placeholder

    monkeypatch.setattr(
        "backend.ai.generation._call_gemini_async",
        _fake_call_gemini_async,
    )

    result = await repair_pipeline_from_error(
        original_yaml=original_yaml,
        failed_step="load_data",
        error_type="Exception",
        error_message="boom",
        file_ids=[],
        db=test_db,
    )

    assert result.valid is False
    assert result.corrected_yaml == ""
    assert result.diff_lines == []
    assert result.error is not None
    assert "quota" in result.error.lower()
