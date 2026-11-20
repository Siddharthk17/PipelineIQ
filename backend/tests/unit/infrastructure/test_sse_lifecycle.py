"""Unit tests for SSE lifecycle constants and streaming headers."""

import backend.api.sse as sse_module


def test_terminal_statuses_are_frozenset():
    assert isinstance(sse_module._TERMINAL_STATUSES, frozenset)


def test_terminal_statuses_include_all_run_terminal_states():
    assert {"COMPLETED", "FAILED", "CANCELLED"}.issubset(
        sse_module._TERMINAL_STATUSES)


def test_terminal_event_types_include_stream_end():
    assert "stream_end" in sse_module._TERMINAL_EVENT_TYPES


def test_heartbeat_interval_is_15_seconds():
    assert sse_module.HEARTBEAT_INTERVAL_SECONDS == 15


def test_sse_headers_disable_buffering_and_cache():
    headers = sse_module._sse_headers()
    assert headers["X-Accel-Buffering"] == "no"
    assert headers["Cache-Control"] == "no-cache, no-transform"
    assert headers["Connection"] == "keep-alive"


def test_format_sse_event_uses_double_newline():
    payload = {"run_id": "abc", "event_type": "progress"}
    event = sse_module._format_sse_event("progress", payload)
    assert event.startswith("event: progress\ndata: ")
    assert event.endswith("\n\n")


def test_extract_event_type_falls_back_to_status_mapping():
    assert sse_module._extract_event_type(
        {"status": "RUNNING"}) == "step_started"
    assert sse_module._extract_event_type(
        {"status": "COMPLETED"}) == "step_completed"


def test_is_terminal_event_true_for_terminal_status():
    assert sse_module._is_terminal_event(
        "progress", {"status": "FAILED"}) is True
    assert sse_module._is_terminal_event(
        "progress", {"status": "RUNNING"}) is False


def test_terminal_statuses_include_violation_and_streaming_stopped():
    """R2-005/C2-010: violation + stopped runs must take the replay path."""
    assert {"CONTRACT_VIOLATION", "STREAMING_STOPPED"}.issubset(
        sse_module._TERMINAL_STATUSES)


def test_terminal_event_type_labels_violation_completed():
    from backend.models import PipelineStatus

    assert sse_module._terminal_event_type(
        PipelineStatus.CONTRACT_VIOLATION) == "pipeline_completed"
    assert sse_module._terminal_event_type(
        PipelineStatus.STREAMING_STOPPED) == "pipeline_cancelled"


def test_settle_to_stopped_publishes_terminal_event(test_db, monkeypatch):
    """R2-005: daemon loop exit to STOPPED must wake SSE watchers."""
    import uuid as uuid_module

    import orjson

    from backend.models import PipelineRun, PipelineStatus
    from backend.tasks import streaming_pipeline as sp

    published = {}

    class FakeRedis:
        def publish(self, channel, payload):
            published["channel"] = channel
            published["payload"] = orjson.loads(payload)

        def setex(self, *args, **kwargs):
            published["cached"] = True

    monkeypatch.setattr(sp, "get_pubsub_redis", lambda: FakeRedis())
    monkeypatch.setattr("backend.db.redis_pools.get_cache_redis",
                        lambda: FakeRedis())

    run = PipelineRun(
        id=uuid_module.uuid4(), name="sse-stop",
        status=PipelineStatus.STREAMING_ACTIVE,
        yaml_config="pipeline:\n  name: s\n  steps: []\n",
    )
    test_db.add(run)
    test_db.commit()
    final = sp._settle_final_status(test_db, run.id)
    assert final == PipelineStatus.STREAMING_STOPPED
    assert published.get("channel") == f"pipeline_progress:{run.id}"
    assert published["payload"]["status"] == "STREAMING_STOPPED"
