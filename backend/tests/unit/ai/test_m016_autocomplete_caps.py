"""Regression tests for M-016: autocomplete must be capped and offloaded.

Bug: suggest_column(s) runs O(n*m) Jaro-Winkler comparisons synchronously
on the event loop with no caps on typed length, column count, or batch
size. A large schema or batch blocks the loop for seconds.
Fix: truncate typed input, cap columns and batch size, and run the CPU
work in a worker thread via asyncio.to_thread.
"""

import asyncio

from backend.ai.autocomplete import (
    MAX_BATCH_ITEMS,
    MAX_COLUMNS,
    MAX_TYPED_LENGTH,
    suggest_column,
    suggest_columns_batch,
)


class TestInputCaps:
    def test_long_typed_truncated_before_matching(self):
        # After truncation to MAX_TYPED_LENGTH the input exactly matches the
        # column, so no suggestion is needed. Uncapped code scores the full
        # 356-char string and returns a spurious suggestion.
        col = "a" * MAX_TYPED_LENGTH
        assert suggest_column(col + "b" * 100, [col]) is None

    def test_columns_capped(self):
        beyond = [f"nomatch{i:05d}" for i in range(MAX_COLUMNS + 100)]
        cols = [*beyond, "reveue"]
        # The only close match sits beyond the cap: capped code sees none.
        assert suggest_column("reveuee", cols) is None
        # Control: a match within the cap is still suggested.
        assert suggest_column("reveuee", ["reveue", *beyond]) == "reveue"

    def test_batch_capped(self):
        queries = [f"col{i}" for i in range(MAX_BATCH_ITEMS + 100)]
        result = suggest_columns_batch(queries, ["col0"])
        assert len(result) == MAX_BATCH_ITEMS

    def test_normal_behavior_unchanged(self):
        assert suggest_column("reveue", ["revenue", "id"]) == "revenue"
        assert suggest_column("revenue", ["revenue", "id"]) is None


class TestEndpointOffload:
    def test_column_endpoint_uses_worker_thread(self, client, monkeypatch):
        calls = []
        real_to_thread = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            calls.append(getattr(func, "__name__", repr(func)))
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        resp = client.post(
            "/api/ai/autocomplete/column",
            json={"typed": "reveue", "available_columns": ["revenue", "id"]},
        )
        assert resp.status_code == 200
        assert resp.json()["suggestion"] == "revenue"
        assert calls, "autocomplete must run in a worker thread, not on the loop"

    def test_batch_endpoint_handles_large_schema(self, client, monkeypatch):
        calls = []
        real_to_thread = asyncio.to_thread

        async def spy(func, *args, **kwargs):
            calls.append(1)
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        cols = [f"column_{i:05d}" for i in range(3000)] + ["revenue"]
        resp = client.post(
            "/api/ai/autocomplete/columns",
            json={
                "typed_columns": ["reveue"],
                "available_columns": cols,
            },
        )
        assert resp.status_code == 200
        assert calls, "batch autocomplete must run in a worker thread"
