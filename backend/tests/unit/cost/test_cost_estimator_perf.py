"""M-142: cost estimator must use per-step types, per-type confidence,
and per-step row counts — not global defaults.

Bugs pinned here (all fail before the fix):
1. Parsed steps expose `.step_type` (StepType), not `.type`, so every
   step was estimated with the "load" rate/history.
2. Confidence used the GLOBAL data-point total, so a step type with
   almost no history inherited high confidence from other types.
3. Historical p50 scaling used the global input row max for every step,
   so downstream (post-filter) steps were overestimated.
"""
from types import SimpleNamespace

from backend.pipeline.parser import StepType


def _ns(name, step_type):
    return SimpleNamespace(name=name, step_type=step_type)


class _FakeResult:
    def __init__(self, p50_ms, data_points):
        self._row = SimpleNamespace(p50_ms=p50_ms, data_points=data_points)

    def first(self):
        return self._row


class _FakeDB:
    HIST = {
        "load": (50.0, 40),
        "filter": (100.0, 30),
        "aggregate": (100.0, 2),
    }

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return SimpleNamespace(row_count=90_000)

    def execute(self, sql, params):
        p50, n = self.HIST.get(params["step_type"], (None, 0))
        return _FakeResult(p50, n)


def _estimate(monkeypatch):
    from backend.cost import estimator as est_mod

    pipeline = SimpleNamespace(steps=[
        _ns("load", StepType.LOAD),
        _ns("filter", StepType.FILTER),
        _ns("agg", StepType.AGGREGATE),
    ])
    monkeypatch.setattr(
        "backend.pipeline.cache.get_parsed_pipeline", lambda yaml: pipeline)
    return est_mod.estimate_pipeline_cost("yaml", ["f1"], _FakeDB())


def test_step_types_resolved_from_step_type_attr(monkeypatch):
    est = _estimate(monkeypatch)
    assert [s.step_type for s in est.step_estimates] == [
        "load", "filter", "aggregate"]


def test_confidence_is_per_step_type(monkeypatch):
    est = _estimate(monkeypatch)
    by_type = {s.step_type: s.confidence for s in est.step_estimates}
    assert by_type["filter"] == min(0.9, 30 / 20)
    assert by_type["aggregate"] == min(0.9, 2 / 20)
    assert by_type["aggregate"] < by_type["filter"]


def test_historical_scales_with_step_row_in(monkeypatch):
    est = _estimate(monkeypatch)
    by_type = {s.step_type: s.predicted_ms for s in est.step_estimates}
    # Same p50 (100ms @ ~10k rows): aggregate sees ~30k rows after the
    # filter's //3 reduction, so it must estimate lower than the filter
    # step that sees the full 90k rows.
    assert by_type["aggregate"] < by_type["filter"]
