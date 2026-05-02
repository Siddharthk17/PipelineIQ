"""Regression tests for worker topology in docker-compose."""

from pathlib import Path

import yaml


def _load_compose() -> dict:
    repo_root = Path(__file__).resolve().parents[4]
    compose_path = repo_root / "docker-compose.yml"
    return yaml.safe_load(compose_path.read_text(encoding="utf-8"))


def test_worker_gemini_joins_pipelineiq_network():
    compose = _load_compose()
    worker_gemini = compose["services"]["worker-gemini"]

    assert "pipelineiq-network" in worker_gemini.get("networks", [])


def test_worker_default_consumes_critical_and_default():
    compose = _load_compose()
    worker_default = compose["services"]["worker-default"]

    assert "--queues=critical,default" in worker_default["command"]
