"""Tests for Gemini client model fallback behavior."""

from types import SimpleNamespace

import pytest

from backend.clients.gemini_client import GeminiModelAdapter


class _FakeModels:
    def __init__(self):
        self.calls: list[str] = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model == "gemini-1.5-flash":
            raise Exception(
                "404 NOT_FOUND. {'error': {'message': "
                "'models/gemini-1.5-flash is not found for API version v1beta'}}"
            )
        return SimpleNamespace(text=f"ok from {model}")


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


class TestGeminiModelFallback:
    def test_falls_back_to_next_model_on_not_found(self):
        client = _FakeClient()
        adapter = GeminiModelAdapter(
            client=client,
            model_names=["gemini-1.5-flash", "gemini-2.5-flash"],
        )

        response = adapter.generate_content("Generate pipeline")

        assert response.text == "ok from gemini-2.5-flash"
        assert client.models.calls == ["gemini-1.5-flash", "gemini-2.5-flash"]

    def test_does_not_fallback_on_non_not_found_error(self):
        class _QuotaModels:
            def __init__(self):
                self.calls: list[str] = []

            def generate_content(self, model, contents, config):
                self.calls.append(model)
                raise Exception("429 RESOURCE_EXHAUSTED")

        quota_client = SimpleNamespace(models=_QuotaModels())
        adapter = GeminiModelAdapter(
            client=quota_client,
            model_names=["gemini-2.5-flash", "gemini-2.0-flash"],
        )

        with pytest.raises(Exception, match="RESOURCE_EXHAUSTED"):
            adapter.generate_content("Generate pipeline")

        assert quota_client.models.calls == ["gemini-2.5-flash"]
