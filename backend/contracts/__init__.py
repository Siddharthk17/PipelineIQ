"""Data contracts — schema & quality validation for pipeline output.

This module exports the validation engine extracted into ``validator.py``.
All public symbols remain importable from ``backend.contracts``.
"""

from backend.contracts.validator import (
    TYPE_CATEGORY_MAP,
    BreachReport,
    ContractValidationResult,
    ContractViolation,
    _arrow_type_category,
    _parse_contract,
    build_breach_report,
    validate_against_contract,
)

__all__ = [
    "BreachReport",
    "ContractViolation",
    "ContractValidationResult",
    "TYPE_CATEGORY_MAP",
    "build_breach_report",
    "validate_against_contract",
    "_arrow_type_category",
    "_parse_contract",
]
