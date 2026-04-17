"""Tests for Jaro-Winkler column autocomplete."""

from backend.ai.autocomplete import (
    SIMILARITY_THRESHOLD,
    suggest_column,
    suggest_columns_batch,
)


COLUMNS = ["revenue", "customer_id", "region", "created_at", "order_count"]


class TestSuggestColumn:
    def test_exact_match_returns_none(self):
        assert suggest_column("revenue", COLUMNS) is None

    def test_clear_typo_returns_expected_column(self):
        assert suggest_column("reveue", COLUMNS) == "revenue"

    def test_transposed_characters_match(self):
        assert suggest_column("customer_di", COLUMNS) == "customer_id"

    def test_completely_unrelated_value_returns_none(self):
        assert suggest_column("xyz", COLUMNS) is None

    def test_empty_inputs_return_none(self):
        assert suggest_column("", COLUMNS) is None
        assert suggest_column("revenue", []) is None

    def test_case_insensitive_exact_match(self):
        assert suggest_column("REVENUE", COLUMNS) == "revenue"

    def test_threshold_is_expected(self):
        assert SIMILARITY_THRESHOLD == 0.85


class TestSuggestColumnsBatch:
    def test_batch_includes_all_queries(self):
        queries = ["reveue", "region", "xyz"]
        suggestions = suggest_columns_batch(queries, COLUMNS)
        assert set(suggestions.keys()) == set(queries)

    def test_batch_typo_and_exact_match_behavior(self):
        suggestions = suggest_columns_batch(["reveue", "region"], COLUMNS)
        assert suggestions["reveue"] == "revenue"
        assert suggestions["region"] is None

