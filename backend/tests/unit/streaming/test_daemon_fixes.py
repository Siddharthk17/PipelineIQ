"""Regression tests for streaming-daemon remediation (w1-daemon).

Each class maps to a finding ID. Tests use fakes (no Kafka/Redpanda)
and run against the hermetic SQLite test DB where persistence matters.
"""

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
    df = pd.DataFrame([{"user_id": "u1", "amount": 50.0}, {"user_id": "u2", "amount": 200.0}])
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

        registry, _output_name = _seed_registry(_table(), _make_consume_step("ingest"))
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


class TestM010CommitGating:
    """Offsets must commit ONLY after successful publish+persist (M-010)."""

    def _fake_msg(self, partition=0, offset=0, value=b'{"amount": 200.0}'):
        m = MagicMock()
        m.topic.return_value = "orders"
        m.partition.return_value = partition
        m.offset.return_value = offset
        m.value.return_value = value
        m.key.return_value = None
        m.error.return_value = None
        return m

    def _fake_producer(self, fail=False):
        calls = {"produce": [], "flush": 0}
        state = {"fail": fail}

        class FakeProducer:
            def produce(
                self, topic, value=None, key=None, headers=None, on_delivery=None, **kwargs
            ):
                calls["produce"].append((topic, value))
                if on_delivery is not None:
                    err = Exception("boom") if state["fail"] else None
                    on_delivery(err, MagicMock())

            def poll(self, timeout=0):
                return 0

            def flush(self, timeout=None):
                calls["flush"] += 1
                return len(calls["produce"]) if state["fail"] else 0

        return FakeProducer(), calls, state

    def _fake_consumer(self):
        calls = {"commit": []}

        class FakeConsumer:
            def commit(self, message=None, offsets=None, asynchronous=True):
                calls["commit"].append((message, offsets, asynchronous))

        return FakeConsumer(), calls

    def _batch_kwargs(self, producer_fail=False):
        from backend.execution.smart_executor import SmartExecutor
        from backend.pipeline.lineage import LineageRecorder

        producer, pcalls, _ = self._fake_producer(fail=producer_fail)
        dlq, dcalls, _ = self._fake_producer(fail=False)
        consumer, ccalls = self._fake_consumer()
        ctx = {
            "consumer": consumer,
            "ccalls": ccalls,
            "producer": producer,
            "pcalls": pcalls,
            "dlq": dlq,
            "dcalls": dcalls,
            "publish_step": MagicMock(topic="out", key_column=None),
            "middle_steps": [_make_filter_step(input="ingest")],
            "consume_step": _make_consume_step("ingest", topic="orders"),
            "executor": SmartExecutor(),
            "recorder": LineageRecorder(),
            "stats": {"batches": 0, "messages": 0, "failed": 0, "dlq": 0, "start": 0.0},
        }
        kwargs = {
            "consumer": consumer,
            "producer": producer,
            "dlq_producer": dlq,
            "consume_step": ctx["consume_step"],
            "publish_step": ctx["publish_step"],
            "middle_steps": ctx["middle_steps"],
            "executor": ctx["executor"],
            "recorder": ctx["recorder"],
            "stats": ctx["stats"],
        }
        return ctx, kwargs

    def test_publish_failure_withholds_offset_commit(self):
        from backend.tasks.streaming_pipeline import _process_batch

        ctx, kwargs = self._batch_kwargs(producer_fail=True)
        msgs = [self._fake_msg(partition=0, offset=41)]
        errors = _process_batch(
            msgs,
            deserialize_fmt="json",
            consecutive_errors=0,
            on_success=lambda **kw: None,
            **kwargs,
        )
        assert errors == 1
        assert ctx["ccalls"]["commit"] == [], (
            "offsets must NOT commit when publish failed (silent loss)"
        )
        assert ctx["stats"]["failed"] == 1

    def test_success_commits_per_partition_high_water(self):
        from backend.tasks.streaming_pipeline import _process_batch

        ctx, kwargs = self._batch_kwargs(producer_fail=False)
        msgs = [
            self._fake_msg(partition=0, offset=41),
            self._fake_msg(partition=0, offset=42),
            self._fake_msg(partition=1, offset=7),
        ]
        successes = []
        errors = _process_batch(
            msgs,
            deserialize_fmt="json",
            consecutive_errors=0,
            on_success=lambda **kw: successes.append(kw),
            **kwargs,
        )
        assert errors == 0
        assert len(ctx["ccalls"]["commit"]) == 1
        _msg, offsets, sync = ctx["ccalls"]["commit"][0]
        assert sync is False
        by_part = {(tp.topic, tp.partition): tp.offset for tp in offsets}
        assert by_part == {("orders", 0): 43, ("orders", 1): 8}, (
            "must commit per-partition high-water (offset+1)"
        )
        assert successes, "on_success persist hook must run on success"

    def test_dlq_failure_withholds_commit(self):
        from backend.tasks.streaming_pipeline import StreamingDLQError, _send_dlq

        prod, _, _ = self._fake_producer(fail=True)
        try:
            _send_dlq(prod, [self._fake_msg()], "orders", "bad batch")
        except StreamingDLQError:
            pass
        else:
            raise AssertionError("_send_dlq must raise when DLQ persist fails")

    def test_publish_delivery_error_raises(self):
        import pandas as pd

        from backend.tasks.streaming_pipeline import StreamingPublishError, _publish

        prod, _, _ = self._fake_producer(fail=True)
        df = pd.DataFrame([{"amount": 1.0}])
        try:
            _publish(prod, df, MagicMock(topic="out", key_column=None))
        except StreamingPublishError:
            pass
        else:
            raise AssertionError("_publish must raise when delivery fails (was swallowed)")


class TestM011StatsUpsert:
    """Restart must not hit a duplicate StreamingStats PK (M-011)."""

    def _make_run(self, db):
        import uuid as uuid_module

        from backend.models import PipelineRun, PipelineStatus

        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="stream-run",
            status=PipelineStatus.STREAMING_STOPPED,
            yaml_config="pipeline:\n  name: s\n  steps: []\n",
        )
        db.add(run)
        db.commit()
        return run

    def test_double_init_does_not_raise(self, test_db):
        from backend.models import StreamingStats
        from backend.tasks.streaming_pipeline import _init_stats

        run = self._make_run(test_db)
        _init_stats(test_db, run.id, "orders", "g1")
        test_db.commit()
        _init_stats(test_db, run.id, "orders", "g1")
        test_db.commit()
        rows = test_db.query(StreamingStats).filter(StreamingStats.run_id == run.id).all()
        assert len(rows) == 1

    def test_restart_resets_counters_and_updates_group(self, test_db):
        from backend.models import StreamingStats
        from backend.tasks.streaming_pipeline import _init_stats, _update_stats

        run = self._make_run(test_db)
        _init_stats(test_db, run.id, "orders", "g1")
        test_db.commit()
        stats = {"batches": 10, "messages": 500, "failed": 3, "dlq": 2, "start": 0.0}
        _update_stats(test_db, run.id, stats, 50.0, 12.5)
        # Restart: same run_id must reset, not raise.
        _init_stats(test_db, run.id, "orders", "g2")
        test_db.commit()
        row = test_db.query(StreamingStats).filter(StreamingStats.run_id == run.id).one()
        assert row.consumer_group == "g2"
        assert row.batches_processed == 0
        assert row.messages_processed == 0
        assert row.messages_failed == 0
        assert row.messages_dlq == 0
