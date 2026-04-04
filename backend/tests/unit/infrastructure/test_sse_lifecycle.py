"""Unit tests for SSE lifecycle constants and streaming headers."""

import backend.api.sse as sse_module


def test_terminal_statuses_are_frozenset():
    assert isinstance(sse_module._TERMINAL_STATUSES, frozenset)


def test_terminal_statuses_include_all_run_terminal_states():
    assert {"COMPLETED", "FAILED", "CANCELLED"}.issubset(sse_module._TERMINAL_STATUSES)


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
    assert sse_module._extract_event_type({"status": "RUNNING"}) == "step_started"
    assert sse_module._extract_event_type({"status": "COMPLETED"}) == "step_completed"


def test_is_terminal_event_true_for_terminal_status():
    assert sse_module._is_terminal_event("progress", {"status": "FAILED"}) is True
    assert sse_module._is_terminal_event("progress", {"status": "RUNNING"}) is False
