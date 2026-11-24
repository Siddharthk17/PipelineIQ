"""Regression tests for readv3-correctness N1.

N1 (Medium): sort ``ascending`` length mismatch passed validation but
raised ValueError at runtime on BOTH engines. Single-element broadcast
([true] + N cols) is intentionally valid on both engines and stays valid.
"""

from backend.pipeline.parser import PipelineParser


def _yaml(by: str, ascending: str) -> str:
    return (
        "pipeline:\n  name: p\n  steps:\n"
        "    - name: l\n      type: load\n      file_id: f1\n"
        "    - name: s\n      type: sort\n      input: l\n"
        f"      by: {by}\n      ascending: {ascending}\n"
        "    - name: out\n      type: save\n      input: s\n      filename: o.csv\n"
    )


def _validate(by: str, ascending: str):
    parser = PipelineParser()
    config = parser.parse(_yaml(by, ascending))
    return parser.validate(config, {"f1"})


def test_n1_ascending_too_long_is_invalid():
    result = _validate("[a, b]", "[true, false, true]")
    assert result.is_valid is False
    assert any(
        e.step_name == "s" and e.field == "ascending" for e in result.errors
    )


def test_n1_ascending_too_short_is_invalid():
    result = _validate("[a, b, c]", "[true, false]")
    assert result.is_valid is False
    assert any(
        e.step_name == "s" and e.field == "ascending" for e in result.errors
    )


def test_n1_ascending_matching_length_stays_valid():
    result = _validate("[a, b]", "[true, false]")
    assert result.is_valid is True


def test_n1_single_element_broadcast_stays_valid():
    # Both engines broadcast one value to all by-columns — keep valid.
    result = _validate("[a, b]", "[true]")
    assert result.is_valid is True


def test_n1_scalar_ascending_stays_valid():
    result = _validate("[a, b]", "true")
    assert result.is_valid is True


def test_n1_single_column_single_value_stays_valid():
    result = _validate("a", "[false]")
    assert result.is_valid is True
