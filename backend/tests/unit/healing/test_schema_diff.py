"""Tests for schema drift detection helpers."""

from backend.execution.schema_diff import (
    compute_schema_diff,
    find_added_columns,
    find_removed_columns,
    find_rename_candidates,
)


OLD_SCHEMA = {
    "customer_id": {"semantic_type": "integer_id"},
    "revenue": {"semantic_type": "currency"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
}

NEW_SCHEMA_RENAMED = {
    "customer_id": {"semantic_type": "integer_id"},
    "rev_usd": {"semantic_type": "currency"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
}

NEW_SCHEMA_ADDED = {
    "customer_id": {"semantic_type": "integer_id"},
    "revenue": {"semantic_type": "currency"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
    "country": {"semantic_type": "categorical"},
}


def test_find_removed_columns_detects_missing_column():
    assert find_removed_columns(OLD_SCHEMA, NEW_SCHEMA_RENAMED) == ["revenue"]


def test_find_added_columns_detects_new_column():
    assert find_added_columns(OLD_SCHEMA, NEW_SCHEMA_ADDED) == ["country"]


def test_find_rename_candidates_ranks_revenue_to_rev_usd():
    candidates = find_rename_candidates(OLD_SCHEMA, NEW_SCHEMA_RENAMED)
    assert candidates
    assert candidates[0]["old_name"] == "revenue"
    assert candidates[0]["new_name"] == "rev_usd"
    assert candidates[0]["similarity"] >= 0.8
    assert candidates[0]["type_match"] is True


def test_compute_schema_diff_sets_summary_and_flags_changes():
    diff = compute_schema_diff(OLD_SCHEMA, NEW_SCHEMA_RENAMED)
    assert diff["has_changes"] is True
    assert "revenue" in diff["removed_columns"]
    assert "rev_usd" in diff["added_columns"]
    assert "revenue" in diff["summary"] or "rev_usd" in diff["summary"]


def test_compute_schema_diff_reports_no_changes_for_identical_schemas():
    diff = compute_schema_diff(OLD_SCHEMA, OLD_SCHEMA)
    assert diff["has_changes"] is False
    assert diff["removed_columns"] == []
    assert diff["added_columns"] == []
    assert diff["renamed_candidates"] == []
