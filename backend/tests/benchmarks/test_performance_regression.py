"""Performance regression guardrails based on committed benchmark baselines."""

from __future__ import annotations

import json
from pathlib import Path


RESULTS_PATH = Path(__file__).resolve().parents[3] / "benchmark" / "results.json"
REQUIRED_OPS = {"filter", "aggregate", "join", "sort", "sql_projection"}


def _load_results() -> dict:
    return json.loads(RESULTS_PATH.read_text(encoding="utf-8"))


def test_benchmark_results_file_exists() -> None:
    assert RESULTS_PATH.exists(), f"Missing benchmark baseline: {RESULTS_PATH}"


def test_benchmark_results_include_required_operations() -> None:
    payload = _load_results()
    largest_size = str(payload["summary"]["largest_size"])
    ops = set(payload["results"][largest_size].keys())
    assert REQUIRED_OPS.issubset(ops)


def test_duckdb_speedup_baseline_is_not_regressed() -> None:
    payload = _load_results()
    largest_size = str(payload["summary"]["largest_size"])
    operations = payload["results"][largest_size]
    speedups = [float(metrics["speedup"]) for metrics in operations.values()]
    faster_ops = sum(1 for value in speedups if value > 1.0)

    assert payload["summary"]["average_speedup_largest"] >= 1.1
    assert min(speedups) >= 0.5
    assert faster_ops >= 3
