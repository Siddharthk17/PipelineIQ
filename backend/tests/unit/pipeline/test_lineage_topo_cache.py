"""M-084: lineage graph queries must not rebuild topological order per query.

get_column_ancestry() and to_react_flow_format() each ran a full
nx.topological_sort (plus a pop(0) O(n^2) BFS queue in ancestry).
The order is cached with a version counter bumped on every mutation,
so repeated queries on an unchanged graph reuse it (M-140: keys are
consistent — this changes cost, not correctness).
"""
from backend.pipeline.lineage import LineageRecorder


def _build_recorder(cols=8, steps=6):
    rec = LineageRecorder()
    columns = [f"c{i}" for i in range(cols)]
    rec.record_load("f1", "data.csv", "load", columns,
                    {c: "int64" for c in columns})
    prev = "load"
    for s in range(steps):
        name = f"step{s}"
        rec.record_passthrough(name, "filter", prev, columns)
        prev = name
    return rec, prev, columns


def test_ancestry_results_unchanged_by_caching():
    rec, last, columns = _build_recorder()
    for col in columns:
        lineage = rec.get_column_ancestry(last, col)
        assert lineage.source_file == "data.csv"
        assert lineage.source_column == col
        assert lineage.total_steps == 1 + 6  # load + 6 passthroughs


def test_topo_order_reused_across_queries():
    rec, last, _ = _build_recorder()
    rec.get_column_ancestry(last, "c0")
    first_cache = rec._topo_cache
    assert first_cache is not None
    rec.get_column_ancestry(last, "c1")
    rec.to_react_flow_format()
    assert rec._topo_cache is first_cache


def test_mutation_invalidates_topo_cache():
    rec, last, columns = _build_recorder()
    rec.get_column_ancestry(last, "c0")
    assert rec._topo_cache is not None
    rec.record_passthrough("extra", "sort", last, columns)
    assert rec._topo_cache is None  # invalidated
    lineage = rec.get_column_ancestry("extra", "c0")
    assert lineage.total_steps == 1 + 6 + 1
    assert rec._topo_cache is not None  # rebuilt


def test_react_flow_layout_stable():
    rec, _last, _ = _build_recorder()
    g1 = rec.to_react_flow_format()
    g2 = rec.to_react_flow_format()
    assert [(n.id, n.position) for n in g1.nodes] == [
        (n.id, n.position) for n in g2.nodes]
    assert len(g1.edges) == len(g2.edges)
