"""Unit tests for streaming components — no Redpanda required."""

import inspect
import json
from unittest.mock import MagicMock

import pytest


class TestDefaultConstants:
    def test_default_partitions_is_8(self):
        from backend.streaming.redpanda_client import DEFAULT_PARTITIONS
        assert DEFAULT_PARTITIONS == 8, (
            f"Bottleneck #15 fix requires DEFAULT_PARTITIONS=8, got {DEFAULT_PARTITIONS}")

    def test_retention_is_24h(self):
        from backend.streaming.redpanda_client import DEFAULT_RETENTION_MS
        assert DEFAULT_RETENTION_MS == "86400000"

    def test_dlq_producer_uses_acks_all(self):
        src = inspect.getsource(
            __import__(
                "backend.streaming.redpanda_client",
                fromlist=["make_dlq_producer"]).make_dlq_producer)
        assert '"all"' in src or "'all'" in src, (
            "DLQ producer must use acks='all'")


class TestDeserialize:
    def test_json_batch_to_dataframe(self):
        from backend.tasks.streaming_pipeline import _deserialize
        msgs = []
        for d in [{"user_id": "u1", "amount": 100.0},
                  {"user_id": "u2", "amount": 200.0}]:
            m = MagicMock()
            m.value.return_value = json.dumps(d).encode()
            m.error.return_value = None
            msgs.append(m)
        df = _deserialize(msgs, "json")
        assert len(df) == 2
        assert "user_id" in df.columns

    def test_malformed_json_skipped(self):
        from backend.tasks.streaming_pipeline import _deserialize
        msgs = []
        for raw in [b'{"x": 1}', b'NOT_JSON{{{', b'{"y": 2}']:
            m = MagicMock()
            m.value.return_value = raw
            m.error.return_value = None
            msgs.append(m)
        df = _deserialize(msgs, "json")
        assert len(df) == 2

    def test_empty_batch_returns_empty(self):
        from backend.tasks.streaming_pipeline import _deserialize
        df = _deserialize([], "json")
        assert df.empty

    def test_null_value_skipped(self):
        from backend.tasks.streaming_pipeline import _deserialize
        msgs = []
        m1 = MagicMock()
        m1.value.return_value = None
        m1.error.return_value = None
        msgs.append(m1)
        m2 = MagicMock()
        m2.value.return_value = json.dumps({"ok": True}).encode()
        m2.error.return_value = None
        msgs.append(m2)
        df = _deserialize(msgs, "json")
        assert len(df) == 1


class TestStepTypeRegistration:
    def test_stream_consume_registered(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert "stream_consume" in STEP_DEFINITIONS

    def test_stream_publish_registered(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert "stream_publish" in STEP_DEFINITIONS

    def test_total_is_19(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert len(STEP_DEFINITIONS) == 19, (
            f"Expected 19 step types after Week 9, got {len(STEP_DEFINITIONS)}")

    def test_stream_consume_is_source(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert STEP_DEFINITIONS["stream_consume"]["isSource"] is True
        assert STEP_DEFINITIONS["stream_consume"]["maxInputs"] == 0

    def test_stream_publish_is_terminal(self):
        from backend.pipeline.definitions import STEP_DEFINITIONS
        assert STEP_DEFINITIONS["stream_publish"]["isTerminal"] is True


class TestStreamingTaskConfig:
    def test_on_streaming_queue(self):
        from backend.tasks.streaming_pipeline import run_streaming_pipeline
        assert run_streaming_pipeline.queue == "streaming"

    def test_acks_late_true(self):
        from backend.tasks.streaming_pipeline import run_streaming_pipeline
        assert getattr(run_streaming_pipeline, "acks_late", False) is True

    def test_no_time_limit(self):
        from backend.tasks.streaming_pipeline import run_streaming_pipeline
        assert getattr(run_streaming_pipeline, "time_limit", None) is None, (
            "Streaming task must NOT have time_limit — it runs indefinitely")
        assert getattr(run_streaming_pipeline, "soft_time_limit", None) is None, (
            "Streaming task must NOT have soft_time_limit — it runs indefinitely")

    def test_consumer_closed_in_finally(self):
        from backend.tasks.streaming_pipeline import run_streaming_pipeline
        src = inspect.getsource(run_streaming_pipeline)
        assert "finally:" in src, "Streaming task must have finally block"
        assert "consumer.close()" in src, (
            "consumer.close() must be in finally block")


class TestDLQPublishing:
    def test_dlq_headers_present(self):
        from backend.tasks.streaming_pipeline import _send_dlq
        src = inspect.getsource(_send_dlq)
        assert "x-error" in src
        assert "x-original-topic" in src
        assert "x-failed-at" in src

    def test_dlq_topic_naming(self):
        from backend.tasks.streaming_pipeline import _send_dlq
        src = inspect.getsource(_send_dlq)
        assert ".dlq" in src, "DLQ must route to {topic}.dlq"


class TestStreamingControlRouter:
    def test_streaming_routes_registered(self):
        from backend.main import app
        routes = [r.path for r in app.routes]
        assert any("/api/streaming" in r for r in routes)
        assert any("pause" in r for r in routes)
        assert any("stop" in r for r in routes)
        assert any("dlq" in r for r in routes)


class TestPipelineStatusStreaming:
    def test_streaming_active_exists(self):
        from backend.models import PipelineStatus
        assert hasattr(PipelineStatus, "STREAMING_ACTIVE")
        assert PipelineStatus.STREAMING_ACTIVE.value == "STREAMING_ACTIVE"

    def test_streaming_paused_exists(self):
        from backend.models import PipelineStatus
        assert hasattr(PipelineStatus, "STREAMING_PAUSED")

    def test_streaming_stopped_exists(self):
        from backend.models import PipelineStatus
        assert hasattr(PipelineStatus, "STREAMING_STOPPED")


class TestStreamingStatsModel:
    def test_model_imports(self):
        from backend.models import StreamingStats
        assert StreamingStats.__tablename__ == "streaming_stats"

    def test_model_has_required_columns(self):
        from backend.models import StreamingStats
        cols = {c.name for c in StreamingStats.__table__.columns}
        required = {
            "run_id", "batches_processed", "messages_processed",
            "messages_failed", "messages_dlq", "throughput_per_sec",
            "consumer_lag", "last_batch_at", "started_at",
            "topic", "consumer_group", "avg_batch_latency_ms",
        }
        assert required.issubset(cols), f"Missing columns: {required - cols}"
