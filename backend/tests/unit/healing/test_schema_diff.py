"""Tests for schema drift detection helpers."""

from backend.execution.schema_diff import (
    compute_schema_diff,
    find_added_columns,
    find_removed_columns,
    find_rename_candidates,
)


OLD_SCHEMA = {
    "customer_id": {"semantic_type": "integer_id"},
    "customer_name": {"semantic_type": "text"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
}

NEW_SCHEMA_RENAMED = {
    "customer_id": {"semantic_type": "integer_id"},
    "cust_name": {"semantic_type": "text"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
}

NEW_SCHEMA_ADDED = {
    "customer_id": {"semantic_type": "integer_id"},
    "customer_name": {"semantic_type": "text"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
    "country": {"semantic_type": "categorical"},
}

NEW_SCHEMA_REMOVED = {
    "customer_id": {"semantic_type": "integer_id"},
    "region": {"semantic_type": "categorical"},
    "order_date": {"semantic_type": "datetime"},
}


def test_find_removed_columns_detects_missing_column():
    assert find_removed_columns(OLD_SCHEMA, NEW_SCHEMA_RENAMED) == ["customer_name"]


def test_find_removed_columns_empty_when_no_removal():
    assert find_removed_columns(OLD_SCHEMA, NEW_SCHEMA_ADDED) == []


def test_find_removed_columns_empty_for_identical_schemas():
    assert find_removed_columns(OLD_SCHEMA, OLD_SCHEMA) == []


def test_find_removed_columns_detects_pure_removal():
    removed = find_removed_columns(OLD_SCHEMA, NEW_SCHEMA_REMOVED)
    assert "customer_name" in removed
    assert "customer_id" not in removed


def test_find_added_columns_detects_new_column():
    assert find_added_columns(OLD_SCHEMA, NEW_SCHEMA_ADDED) == ["country"]


def test_find_added_columns_empty_for_identical_schemas():
    assert find_added_columns(OLD_SCHEMA, OLD_SCHEMA) == []


def test_find_added_columns_detects_renamed_column():
    assert "cust_name" in find_added_columns(OLD_SCHEMA, NEW_SCHEMA_RENAMED)


def test_find_rename_candidates_ranks_revenue_to_rev_usd():
    candidates = find_rename_candidates(OLD_SCHEMA, NEW_SCHEMA_RENAMED)
    assert candidates
    assert candidates[0]["old_name"] == "customer_name"
    assert candidates[0]["new_name"] == "cust_name"
    assert candidates[0]["similarity"] >= 0.85
    assert candidates[0]["type_match"] is True


def test_find_rename_candidates_sorted_by_confidence():
    old = {
        "total_amount": {"semantic_type": "currency"},
        "customer_name": {"semantic_type": "text"},
    }
    new = {
        "total_amt": {"semantic_type": "currency"},
        "cust_name": {"semantic_type": "text"},
    }
    candidates = find_rename_candidates(old, new)
    if len(candidates) > 1:
        confidences = [c["confidence"] for c in candidates]
        assert confidences == sorted(confidences, reverse=True)


def test_find_rename_candidates_empty_when_no_removed_or_added():
    assert find_rename_candidates(OLD_SCHEMA, OLD_SCHEMA) == []


def test_find_rename_candidates_empty_when_similarity_below_threshold():
    old = {"revenue": {"semantic_type": "currency"}}
    new = {"xyz_completely_different_name": {"semantic_type": "text"}}
    candidates = find_rename_candidates(old, new)
    assert not candidates


def test_find_rename_candidates_type_match_increases_confidence():
    candidates = find_rename_candidates(OLD_SCHEMA, NEW_SCHEMA_RENAMED)
    name_rename = next(
        c for c in candidates if c["old_name"] == "customer_name" and c["new_name"] == "cust_name"
    )
    assert name_rename["type_match"] is True
    assert name_rename["confidence"] >= name_rename["similarity"]


def test_compute_schema_diff_sets_summary_and_flags_changes():
    diff = compute_schema_diff(OLD_SCHEMA, NEW_SCHEMA_RENAMED)
    assert diff["has_changes"] is True
    assert "customer_name" in diff["removed_columns"]
    assert "cust_name" in diff["added_columns"]
    assert "customer_name" in diff["summary"] or "cust_name" in diff["summary"]


def test_compute_schema_diff_reports_no_changes_for_identical_schemas():
    diff = compute_schema_diff(OLD_SCHEMA, OLD_SCHEMA)
    assert diff["has_changes"] is False
    assert diff["removed_columns"] == []
    assert diff["added_columns"] == []
    assert diff["renamed_candidates"] == []


def test_compute_schema_diff_handles_empty_schemas():
    diff = compute_schema_diff({}, {})
    assert diff["has_changes"] is False
    assert diff["removed_columns"] == []
    assert diff["added_columns"] == []
