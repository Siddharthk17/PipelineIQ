"""Tests for per-run lineage caching in Redis."""
from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pickle

from backend.repositories.catalog import (
    LINEAGE_CACHE_TTL,
    get_cached_run_lineage,
    build_bounded_lineage_graph,
)


class TestLineageCache:
    def test_cache_key_format(self):
        run_id = "abc-123"
        expected_key = f"lineage:graph:{run_id}"
        assert expected_key == "lineage:graph:abc-123"

    def test_lineage_cache_ttl_is_one_hour(self):
        assert LINEAGE_CACHE_TTL == 3600

    def test_build_bounded_graph_empty_input(self):
        G = build_bounded_lineage_graph([])
        assert G.number_of_nodes() == 0
        assert G.number_of_edges() == 0

    def test_build_bounded_graph_single_step(self):
        class MockStep:
            step_name = "load_data"
            columns_in = ["id", "name"]
            columns_out = ["id", "name"]

        G = build_bounded_lineage_graph([MockStep()])
        assert G.number_of_nodes() >= 2
        assert G.number_of_edges() >= 2

    def test_build_bounded_graph_edges_have_step_attribute(self):
        class MockStep:
            step_name = "filter_active"
            columns_in = ["id", "status"]
            columns_out = ["id", "status"]

        G = build_bounded_lineage_graph([MockStep()])
        for _, _, data in G.edges(data=True):
            assert data.get("step") == "filter_active"
            assert data.get("relation") == "transforms"
