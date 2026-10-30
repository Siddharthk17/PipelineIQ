"""Unit tests for runner memory bounding (S18)."""

from types import SimpleNamespace

from backend.pipeline.runner import _compute_consumer_counts, _store_and_evict


def _step(name, **refs):
    return SimpleNamespace(name=name, **refs)


def test_consumer_counts_diamond():
    steps = [
        _step("load"),
        _step("a", input="load"),
        _step("b", input="load"),
        _step("j", left="a", right="b"),
        _step("out", input="j"),
    ]
    assert _compute_consumer_counts(steps) == {
        "load": 2, "a": 1, "b": 1, "j": 1,
    }


def test_evicts_consumed_tables_but_keeps_needed():
    registry: dict = {}
    remaining = {"load": 2, "a": 1, "b": 1, "j": 1}
    table = object()
    _store_and_evict(registry, "load", table, [], remaining)
    _store_and_evict(registry, "a", table, ["load"], remaining)
    assert "load" in registry  # b still needs it
    _store_and_evict(registry, "b", table, ["load"], remaining)
    assert "load" not in registry  # last consumer ran
    assert set(registry) == {"a", "b"}
    _store_and_evict(registry, "j", table, ["a", "b"], remaining)
    assert set(registry) == {"j"}
    _store_and_evict(registry, "out", table, ["j"], remaining)
    assert set(registry) == {"out"}
