"""Tests for autonomous healing error classification."""

from backend.execution.healing_classifier import (
    HEALABLE_ERROR_TYPES,
    get_healing_scenario,
    is_healable,
)


class ColumnNotFoundError(Exception):
    pass


def test_column_not_found_error_is_healable():
    error = ColumnNotFoundError("Column 'revenue' not found in columns")
    assert is_healable(error) is True


def test_key_error_is_healable():
    assert is_healable(KeyError("revenue")) is True


def test_value_error_type_mismatch_is_healable():
    error = ValueError("cannot convert column to numeric type mismatch")
    assert is_healable(error) is True


def test_type_error_is_healable():
    error = TypeError("type mismatch invalid value")
    assert is_healable(error) is True


def test_division_by_zero_is_not_healable():
    assert is_healable(ValueError("division by zero")) is False


def test_memory_error_is_not_healable():
    assert is_healable(MemoryError("out of memory")) is False


def test_attribute_error_is_not_healable():
    assert is_healable(AttributeError("attribute error")) is False


def test_connection_failure_is_not_healable():
    assert is_healable(ConnectionError("connection refused")) is False


def test_file_not_found_is_not_healable():
    assert is_healable(IOError("file not found")) is False


def test_syntax_error_is_not_healable():
    assert is_healable(SyntaxError("syntax error")) is False


def test_healable_error_types_cover_expected_classes():
    assert "ColumnNotFoundError" in HEALABLE_ERROR_TYPES
    assert "JoinKeyMissingError" in HEALABLE_ERROR_TYPES
    assert "KeyError" in HEALABLE_ERROR_TYPES
    assert "ValueError" in HEALABLE_ERROR_TYPES
    assert "TypeError" in HEALABLE_ERROR_TYPES


def test_get_healing_scenario_returns_correct_scenario():
    error = ColumnNotFoundError("Column 'rev_usd' not found")
    scenario = get_healing_scenario(error)
    assert scenario == "column_renamed_or_removed"


def test_get_healing_scenario_returns_none_for_non_healable():
    assert get_healing_scenario(MemoryError("OOM")) is None


def test_value_error_no_healable_pattern_is_not_healable():
    error = ValueError("some generic math error")
    assert is_healable(error) is False


def test_value_error_with_join_key_is_healable():
    error = ValueError("join key not found in schema")
    assert is_healable(error) is True


def test_healable_error_types_has_all_required_entries():
    required = {
        "ColumnNotFoundError",
        "KeyError",
        "MergeError",
        "ValueError",
        "TypeError",
        "AggregationError",
    }
    assert required <= set(HEALABLE_ERROR_TYPES.keys())
