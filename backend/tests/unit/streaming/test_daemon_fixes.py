"""Regression tests for streaming-daemon remediation (w1-daemon).

Each class maps to a finding ID. Tests use fakes (no Kafka/Redpanda)
and run against the hermetic SQLite test DB where persistence matters.
"""
import json
from unittest.mock import MagicMock

import pandas as pd
import pyarrow as pa


def _make_consume_step(name="ingest", topic="orders"):
    step = MagicMock()
    step.name = name
    step.type = "stream_consume"
    step.step_type = "stream_consume"
    step.topic = topic
    step.consumer_group = None
    step.batch_size = 100
    step.batch_timeout_ms = 5000
    step.deserialize = "json"
    return step


def _make_filter_step(name="big_orders", input="ingest"):
    from backend.pipeline.parser import FilterOperator, FilterStepConfig
    return FilterStepConfig(
        name=name,
        step_type="filter",
        input=input,
        column="amount",
        operator=FilterOperator.GREATER_THAN,
        value=100,
    )


def _table():
    df = pd.DataFrame(
        [{"user_id": "u1", "amount": 50.0},
         {"user_id": "u2", "amount": 200.0}])
    return pa.Table.from_pandas(df, preserve_index=False)


class TestM009RegistrySeeding:
    def test_seed_registers_consume_step_name(self):
        from backend.tasks.streaming_pipeline import _seed_registry
        registry, output_name = _seed_registry(_table(), _make_consume_step("ingest"))
        assert output_name == "ingest"
        assert "ingest" in registry
        assert "_stream_input" in registry

    def test_transform_naming_consume_step_executes(self):
        """A transform with input=<consume step name> must run (was KeyError)."""
        from backend.execution.smart_executor import SmartExecutor
        from backend.pipeline.lineage import LineageRecorder
        from backend.tasks.streaming_pipeline import _seed_registry
        registry, output_name = _seed_registry(_table(), _make_consume_step("ingest"))
        result = SmartExecutor().execute(
            _make_filter_step(input="ingest"),
            registry,
            LineageRecorder(),
        )
        assert result.output_table.num_rows == 1

    def test_unknown_single_input_chains_from_previous_with_warning(self):
        from backend.tasks.streaming_pipeline import _ensure_step_input
        registry = {"ingest": _table()}
        step = _make_filter_step(input="typo_name")
        _ensure_step_input(registry, step, "ingest")
        assert "typo_name" in registry

    def test_join_missing_side_not_aliased(self):
        """Join left/right refs must NOT be silently remapped (real config error)."""
        from backend.tasks.streaming_pipeline import _ensure_step_input
        registry = {"ingest": _table()}
        step = MagicMock()
        step.name = "j"
        step.input = None
        step.left = "missing_left"
        step.right = "ingest"
        _ensure_step_input(registry, step, "ingest")
        assert "missing_left" not in registry
