"""Regression test for R-R-007: DLQ replay must be honest.

A flush failure must surface as an error (not a phantom replayed=N),
and consumed offsets must be committed so a repeated replay resumes
instead of duplicating the whole prefix.
"""
from unittest.mock import MagicMock


class _Msg:
    def __init__(self, topic="t.dlq", partition=0, offset=0, value=b'{"a":1}'):
        self._t, self._p, self._o, self._v = topic, partition, offset, value

    def topic(self):
        return self._t

    def partition(self):
        return self._p

    def offset(self):
        return self._o

    def value(self):
        return self._v

    def key(self):
        return None

    def error(self):
        return None


def _run_replay(monkeypatch, batches, flush_ok=True):
    import backend.routers.streaming as rs

    state = {"produced": [], "commits": 0, "flush_calls": 0}

    class FakeConsumer:
        def subscribe(self, topics):
            pass

        def consume(self, num_messages=100, timeout=1.0):
            if batches:
                return batches.pop(0)
            return []

        def commit(self, *a, **k):
            state["commits"] += 1

        def close(self):
            pass

    class FakeProducer:
        def produce(self, topic, value=None, key=None, on_delivery=None, **kw):
            state["produced"].append((topic, value))
            if on_delivery is not None:
                on_delivery(None, MagicMock())

        def poll(self, timeout=0):
            return 0

        def flush(self, timeout=None):
            state["flush_calls"] += 1
            if not flush_ok:
                raise RuntimeError("flush blew up")

    admin = MagicMock()
    admin.topic_exists.return_value = True
    monkeypatch.setattr(rs, "get_admin_client", lambda: admin)
    monkeypatch.setattr(rs, "make_consumer", lambda *a, **k: FakeConsumer())
    monkeypatch.setattr(rs, "make_producer", FakeProducer)
    return rs._replay_dlq_sync("t", "t.dlq", 100), state


def test_failed_flush_raises_instead_of_lying(monkeypatch):
    import pytest

    import backend.routers.streaming as rs  # noqa: F401 (ensures import)

    with pytest.raises(RuntimeError):
        _run_replay(monkeypatch, [[_Msg()]], flush_ok=False)


def test_success_commits_offsets_for_resume(monkeypatch):
    count, state = _run_replay(
        monkeypatch, [[_Msg(offset=0)], [_Msg(offset=1)], []])
    assert count == 2
    assert state["commits"] >= 1, "offsets must commit so replays resume"
