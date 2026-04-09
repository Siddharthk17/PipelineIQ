"""Unit tests for SmartExecutor routing decisions."""

from types import SimpleNamespace

from backend.execution.smart_executor import ROW_THRESHOLD_DEFAULT, SmartExecutor


def test_should_use_duckdb_for_large_supported_step() -> None:
    router = SmartExecutor(row_threshold=ROW_THRESHOLD_DEFAULT)
    step = SimpleNamespace(step_type="filter")
    assert router.should_use_duckdb(step, ROW_THRESHOLD_DEFAULT + 1) is True


def test_should_not_use_duckdb_for_small_dataset() -> None:
    router = SmartExecutor(row_threshold=ROW_THRESHOLD_DEFAULT)
    step = SimpleNamespace(step_type="aggregate")
    assert router.should_use_duckdb(step, ROW_THRESHOLD_DEFAULT - 1) is False


def test_sql_step_always_routes_to_duckdb() -> None:
    router = SmartExecutor(row_threshold=10_000_000)
    step = SimpleNamespace(step_type="sql")
    assert router.should_use_duckdb(step, 100) is True

