"""
Gemini API client — initialized once at module level.
All Gemini calls go through this module.
Temperature is set per-call, not at initialization.
"""
import os
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

# Initialize once at import — not per request, not per task
_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not _GEMINI_API_KEY:
    logger.warning(
        "GEMINI_API_KEY not set. AI features (generation, repair, autocomplete) "
        "will fail until this environment variable is configured."
    )

if _GEMINI_API_KEY:
    genai.configure(api_key=_GEMINI_API_KEY)

# Use gemini-1.5-flash — generous rate limits on free tier, fast responses
_MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")

def get_gemini_model():
    """Get the configured Gemini model. Raises if API key not set."""
    if not _GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable not set. "
            "Set it in .env and restart the gemini worker."
        )
    return genai.GenerativeModel(_MODEL_NAME)
