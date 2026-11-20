"""Regression tests for M-175: PII redaction + prompt-injection hardening.

User-controlled text (pipeline descriptions, error messages) is sent to
Gemini. It must be scrubbed of PII/secrets first, must never be logged,
and the system prompts must instruct the model to ignore instructions
embedded in the untrusted content.
"""

import pytest

from backend.ai.safety import redact_pii


class TestRedactPii:
    def test_email_redacted(self):
        text = "Contact john.doe@example.com for this sales data"
        redacted = redact_pii(text)
        assert "john.doe@example.com" not in redacted
        assert "[redacted]" in redacted

    def test_phone_redacted(self):
        text = "call +1-555-123-4567 about the pipeline"
        redacted = redact_pii(text)
        assert "555-123-4567" not in redacted

    def test_labeled_secret_redacted(self):
        text = "config has api_key=sk-live-abc123XYZ and works"
        redacted = redact_pii(text)
        assert "sk-live-abc123XYZ" not in redacted

    def test_uuid_file_ids_preserved(self):
        fid = "11111111-1111-1111-1111-111111111111"
        assert fid in redact_pii(f"use file {fid} for the load step")

    def test_plain_text_untouched(self):
        text = "aggregate sales by region and compute totals"
        assert redact_pii(text) == text


class TestPromptRedaction:
    def _fake_gemini(self, monkeypatch, captured):
        from backend.ai import generation as gen

        class _Task:
            def get(self, timeout=None):
                return '{"pipeline": {"name": "t", "steps": []}}'

        class _Client:
            def apply_async(self, args=None, kwargs=None, queue=None):
                captured.append(args[0] if args else "")
                return _Task()

        monkeypatch.setattr(gen, "call_gemini_task", _Client())

    @pytest.mark.asyncio
    async def test_generate_prompt_scrubs_pii(self, test_db, monkeypatch):
        from backend.ai import generation as gen

        captured = []
        self._fake_gemini(monkeypatch, captured)
        await gen.generate_pipeline_from_description(
            description=("Summarize sales for john.doe@example.com by region"),
            file_ids=[],
            db=test_db,
        )
        assert captured, "expected a Gemini call"
        assert "john.doe@example.com" not in captured[0]
        assert "[redacted]" in captured[0]

    @pytest.mark.asyncio
    async def test_repair_prompt_scrubs_pii_and_keeps_file_ids(self, test_db, monkeypatch):
        from backend.ai import generation as gen

        captured = []
        self._fake_gemini(monkeypatch, captured)
        fid = "11111111-1111-1111-1111-111111111111"
        await gen.repair_pipeline_from_error(
            original_yaml="pipeline:\n  name: r\n  steps: []\n",
            failed_step="load_data",
            error_type="ValueError",
            error_message=f"bad column for {fid} reported by jane@corp.io",
            file_ids=[],
            db=test_db,
        )
        assert captured, "expected a Gemini call"
        assert "jane@corp.io" not in captured[0]
        assert fid in captured[0]


class TestPromptInjectionHardening:
    def test_generation_prompt_marks_user_content_untrusted(self):
        from backend.ai.prompts import GENERATION_SYSTEM_PROMPT

        lowered = GENERATION_SYSTEM_PROMPT.lower()
        assert "{user_request}" in GENERATION_SYSTEM_PROMPT
        assert "untrusted" in lowered
        assert "ignore" in lowered

    def test_repair_prompt_marks_user_content_untrusted(self):
        from backend.ai.prompts import REPAIR_SYSTEM_PROMPT

        lowered = REPAIR_SYSTEM_PROMPT.lower()
        assert "untrusted" in lowered
