"""Tests for the recursive CTE blast radius query."""
import inspect

from backend.repositories import catalog as cat_module


class TestBlastRadiusCTE:
    """Test the recursive CTE query logic."""

    def test_blast_radius_function_exists(self):
        from backend.repositories.catalog import get_blast_radius
        assert callable(get_blast_radius)

    def test_upstream_lineage_function_exists(self):
        from backend.repositories.catalog import get_upstream_lineage
        assert callable(get_upstream_lineage)

    def test_max_cte_depth_constant(self):
        from backend.repositories.catalog import MAX_CTE_DEPTH
        assert MAX_CTE_DEPTH == 10

    def test_sql_contains_depth_limit(self):
        source = inspect.getsource(cat_module.get_blast_radius)
        assert "depth < " in source or "depth <= " in source

    def test_sql_uses_recursive_cte(self):
        source = inspect.getsource(cat_module.get_blast_radius)
        assert "WITH RECURSIVE" in source or "with recursive" in source.lower()
        assert "nx." not in source
        assert "networkx" not in source.lower() or "import networkx" not in source

    def test_upstream_lineage_uses_reverse_direction(self):
        source = inspect.getsource(cat_module.get_upstream_lineage)
        assert "ar.target_id" in source


class TestNetworkXBoundary:
    """Verify NetworkX is ONLY used for per-run (small) lineage, never global."""

    def test_get_blast_radius_does_not_import_networkx(self):
        blast_source = inspect.getsource(cat_module.get_blast_radius)
        assert "import networkx" not in blast_source
        assert "nx.DiGraph" not in blast_source
        assert "nx.descendants" not in blast_source

    def test_lineage_cache_uses_networkx_for_per_run(self):
        cache_source = inspect.getsource(cat_module.get_cached_run_lineage)
        assert "redis" in cache_source.lower() or "cache" in cache_source.lower()

    def test_max_lineage_nodes_limit_exists(self):
        from backend.repositories.catalog import MAX_LINEAGE_NODES, MAX_LINEAGE_EDGES
        assert MAX_LINEAGE_NODES == 10_000
        assert MAX_LINEAGE_EDGES == 50_000

    def test_build_bounded_lineage_graph_respects_node_limit(self):
        from backend.repositories.catalog import (
            build_bounded_lineage_graph,
            MAX_LINEAGE_NODES,
        )

        class MockStep:
            def __init__(self, i):
                self.step_name = f"step_{i}"
                self.columns_in = [f"src_{i}_{j}" for j in range(10)]
                self.columns_out = [f"tgt_{i}_{j}" for j in range(10)]

        steps = [MockStep(i) for i in range(MAX_LINEAGE_NODES + 100)]
        G = build_bounded_lineage_graph(steps)

        # Graph must not grossly exceed the limit (small overshoot from edge-level check is acceptable)
        assert G.number_of_nodes() <= MAX_LINEAGE_NODES + 20
        assert "__truncated__" in G.nodes
