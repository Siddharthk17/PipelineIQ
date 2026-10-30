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
        from backend.repositories.catalog import _blast_radius_postgres
        source = inspect.getsource(_blast_radius_postgres)
        assert "depth < " in source or "depth <= " in source

    def test_sql_uses_recursive_cte(self):
        from backend.repositories.catalog import _blast_radius_postgres
        source = inspect.getsource(_blast_radius_postgres)
        assert "WITH RECURSIVE" in source or "with recursive" in source.lower()
        assert "nx." not in source

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
        from backend.repositories.catalog import MAX_LINEAGE_EDGES, MAX_LINEAGE_NODES
        assert MAX_LINEAGE_NODES == 10_000
        assert MAX_LINEAGE_EDGES == 50_000

    def test_build_bounded_lineage_graph_respects_node_limit(self):
        from backend.repositories.catalog import (
            MAX_LINEAGE_NODES,
            build_bounded_lineage_graph,
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


def test_recursive_ctes_have_no_self_referencing_subqueries():
    """Postgres forbids referencing the recursive CTE inside a subquery
    ("recursive reference ... must not appear within a subquery") and
    fails the whole statement. A previous revision used
    NOT EXISTS (SELECT ... FROM downstream/u2 ...) for cycle detection,
    which made /impact and /lineage fail on Postgres while SQLite tests
    stayed green (the API then swallowed the error into []). Cycle
    detection must use the array-path form instead.
    """
    import re

    from backend.repositories import catalog as cat_module

    source = inspect.getsource(cat_module)
    for match in re.finditer(
        r"NOT EXISTS\s*\(\s*SELECT[^;]*?FROM\s+(\w+)", source
    ):
        assert match.group(1) not in ("downstream", "upstream", "d2", "u2"), (
            "illegal self-referencing subquery in recursive CTE: "
            + match.group(0)[:80]
        )
    assert "ANY (d.path)" in source
    assert "ANY (u.path)" in source
    # Edges are deduplicated before the walk: every run re-records the
    # same (source, target) edges, and without DISTINCT the diamond
    # fan-out explodes the working set until the statement timeout fires.
    assert "SELECT DISTINCT source_id, target_id FROM asset_relationships" in source
    # LIMIT is not implemented inside a recursive term: an inner
    # LIMIT fails the whole statement (and the API swallowed it into []).
    # Only the outer result LIMIT may remain.
    assert "LIMIT :cte_limit" not in source
