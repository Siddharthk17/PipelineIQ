"""Tests for autonomous healing error classification."""

from backend.execution.healing_classifier import HEALABLE_ERROR_TYPES, is_healable


def test_column_not_found_error_is_healable():
    class ColumnNotFoundError(Exception):
        pass

    error = ColumnNotFoundError("Column 'revenue' not found in columns")
    assert is_healable(error) is True


def test_key_error_is_healable():
    assert is_healable(KeyError("revenue")) is True


def test_value_error_type_mismatch_is_healable():
    error = ValueError("cannot convert column to numeric type mismatch")
    assert is_healable(error) is True


def test_division_by_zero_is_not_healable():
    assert is_healable(ValueError("division by zero")) is False


def test_memory_error_is_not_healable():
    assert is_healable(MemoryError("out of memory")) is False


def test_attribute_error_is_not_healable():
    assert is_healable(AttributeError("attribute error")) is False


def test_connection_failure_is_not_healable():
    assert is_healable(ConnectionError("connection refused")) is False


def test_healable_error_types_cover_expected_classes():
    assert "ColumnNotFoundError" in HEALABLE_ERROR_TYPES
    assert "JoinKeyMissingError" in HEALABLE_ERROR_TYPES
    assert "KeyError" in HEALABLE_ERROR_TYPES
