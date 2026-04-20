"""Gemini API client with model fallback support."""

import logging
from typing import Any

from google import genai
from google.genai import types

from backend.config import settings

logger = logging.getLogger(__name__)

_GEMINI_CLIENT: genai.Client | None = None
_GEMINI_CLIENT_API_KEY: str | None = None

_MODEL_NOT_FOUND_MARKERS = (
    "not_found",
    "not found for API version",
    "not supported for generatecontent",
)


def _model_not_found_error(exc: Exception) -> bool:
    message = str(exc)
    lower = message.lower()
    return "404" in message and any(marker in lower for marker in _MODEL_NOT_FOUND_MARKERS)


def _configured_model_candidates() -> list[str]:
    configured = [settings.GEMINI_MODEL, settings.GEMINI_FALLBACK_MODELS]
    fallbacks: list[str] = []
    for raw_value in configured:
        for model_name in str(raw_value).split(","):
            stripped = model_name.strip()
            if stripped:
                fallbacks.append(stripped)

    # Keep a safety fallback chain in case env config points to a retired model.
    fallbacks.extend(["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"])

    deduped: list[str] = []
    seen: set[str] = set()
    for model_name in fallbacks:
        if model_name not in seen:
            deduped.append(model_name)
            seen.add(model_name)
    return deduped


def _get_gemini_client() -> genai.Client:
    global _GEMINI_CLIENT, _GEMINI_CLIENT_API_KEY

    api_key = settings.GEMINI_API_KEY.strip()
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable not set. "
            "Set it in .env and restart the gemini worker."
        )

    if _GEMINI_CLIENT is None or _GEMINI_CLIENT_API_KEY != api_key:
        _GEMINI_CLIENT = genai.Client(api_key=api_key)
        _GEMINI_CLIENT_API_KEY = api_key
    return _GEMINI_CLIENT


class GeminiModelAdapter:
    """Adapter exposing generate_content() for task code compatibility."""

    def __init__(self, client: genai.Client, model_names: list[str]):
        self._client = client
        self._model_names = model_names

    def generate_content(
        self, prompt: str, generation_config: dict[str, Any] | None = None
    ) -> Any:
        config = (
            types.GenerateContentConfig(**generation_config)
            if generation_config
            else None
        )
        last_error: Exception | None = None

        for model_name in self._model_names:
            try:
                return self._client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
            except Exception as exc:
                if _model_not_found_error(exc):
                    last_error = exc
                    logger.warning(
                        "Gemini model '%s' unavailable; trying next fallback model.",
                        model_name,
                    )
                    continue
                raise

        # Only reachable when all candidates fail with NOT_FOUND-like errors.
        if last_error:
            raise last_error
        raise RuntimeError("No Gemini model candidates were configured.")


def get_gemini_model() -> GeminiModelAdapter:
    """Get Gemini model adapter. Raises if API key is not configured."""
    return GeminiModelAdapter(
        client=_get_gemini_client(),
        model_names=_configured_model_candidates(),
    )
