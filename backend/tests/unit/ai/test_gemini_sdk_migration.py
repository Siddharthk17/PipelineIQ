"""Regression tests to prevent deprecated Gemini SDK imports."""

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def test_no_backend_gemini_module_uses_deprecated_sdk():
    repo_root = _repo_root()
    candidate_files = [
        repo_root / "backend/clients/gemini_client.py",
        repo_root / "backend/tasks/gemini_tasks.py",
    ]

    for file_path in candidate_files:
        source = file_path.read_text(encoding="utf-8")
        assert "google.generativeai" not in source
