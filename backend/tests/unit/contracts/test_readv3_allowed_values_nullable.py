"""Regression tests for readv3-correctness B1.

B1 (Medium): ``allowed_values`` flagged NULL even on ``nullable: true``
columns — str(None)="None" is never in the allowed set, so a nullable
enum column could never pass with NULLs present. NULLs are governed by
the ``nullable``/``not_null`` rule, not by the enum.
"""

import pyarrow as pa

from backend.contracts.validator import validate_against_contract


def _contract(nullable: bool) -> str:
    return (
        "columns:\n"
        "  s:\n"
        "    type: string\n"
        f"    nullable: {'true' if nullable else 'false'}\n"
        "    allowed_values: [a, b]\n"
    )


def test_b1_nullable_allowed_values_ignores_nulls():
    table = pa.table({"s": pa.array(["a", None, "b"], type=pa.string())})
    result = validate_against_contract(table, _contract(nullable=True))
    assert result.passed is True
    assert result.violations == []


def test_b1_non_nullable_nulls_still_flagged_not_null():
    table = pa.table({"s": pa.array(["a", None, "b"], type=pa.string())})
    result = validate_against_contract(table, _contract(nullable=False))
    assert result.passed is False
    rules = {v.rule for v in result.violations}
    # The not_null path (validator.py) must keep flagging NULLs.
    assert "not_null" in rules


def test_b1_nullable_still_flags_real_disallowed_values():
    table = pa.table({"s": pa.array(["a", None, "zzz"], type=pa.string())})
    result = validate_against_contract(table, _contract(nullable=True))
    assert result.passed is False
    allowed = [v for v in result.violations if v.rule == "allowed_values"]
    assert len(allowed) == 1
    assert "zzz" in allowed[0].message
    assert "None" not in allowed[0].message
