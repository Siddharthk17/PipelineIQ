"""Data contracts — schema & quality validation for pipeline output.

This module exports the validation engine extracted into ``validator.py``.
All public symbols remain importable from ``backend.contracts``.
"""

from backend.contracts.enforcement import (
    block_downstream_schedules,
    find_matching_schedules,
)
from backend.contracts.validator import (
    KNOWN_TYPE_CATEGORIES,
    KNOWN_TYPE_NAMES,
    TYPE_CATEGORY_MAP,
    BreachReport,
    ContractValidationResult,
    ContractViolation,
    _arrow_type_category,
    _check_pattern,
    _is_nullable,
    _parse_contract,
    build_breach_report,
    validate_against_contract,
)

__all__ = [
    "KNOWN_TYPE_CATEGORIES",
    "KNOWN_TYPE_NAMES",
    "TYPE_CATEGORY_MAP",
    "BreachReport",
    "ContractValidationResult",
    "ContractViolation",
    "_arrow_type_category",
    "_check_pattern",
    "_is_nullable",
    "_parse_contract",
    "block_downstream_schedules",
    "build_breach_report",
    "find_matching_schedules",
    "validate_against_contract",
]
