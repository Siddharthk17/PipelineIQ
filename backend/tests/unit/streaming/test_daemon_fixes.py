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


class TestM042SingleFlight:
    """Daemon entry needs a status guard + single-flight claim (M-042)."""

    def _run(self, db, status, task_id=None):
        import uuid as uuid_module

        from backend.models import PipelineRun
        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="stream-run",
            status=status,
            yaml_config="pipeline:\n  name: s\n  steps: []\n",
            celery_task_id=task_id,
        )
        db.add(run)
        db.commit()
        return run

    def test_pending_claimed_active(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _claim_run
        run = self._run(test_db, PipelineStatus.PENDING)
        assert _claim_run(test_db, run.id, "task-1") is None
        test_db.expire_all()
        assert run.status == PipelineStatus.STREAMING_ACTIVE
        assert run.celery_task_id == "task-1"

    def test_duplicate_task_id_rejected(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _claim_run
        run = self._run(
            test_db, PipelineStatus.STREAMING_ACTIVE, task_id="task-1")
        reason = _claim_run(test_db, run.id, "task-2")
        assert reason is not None and "duplicate" in reason.lower()
        test_db.expire_all()
        assert run.celery_task_id == "task-1"

    def test_restart_path_matching_task_id_ok(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _claim_run
        run = self._run(
            test_db, PipelineStatus.STREAMING_ACTIVE, task_id="task-9")
        assert _claim_run(test_db, run.id, "task-9") is None

    def test_stopped_claimed_for_restart(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _claim_run
        run = self._run(test_db, PipelineStatus.STREAMING_STOPPED)
        assert _claim_run(test_db, run.id, "task-7") is None
        test_db.expire_all()
        assert run.status == PipelineStatus.STREAMING_ACTIVE

    def test_terminal_state_rejected_without_mutation(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _claim_run
        run = self._run(test_db, PipelineStatus.COMPLETED)
        reason = _claim_run(test_db, run.id, "task-1")
        assert reason is not None
        test_db.expire_all()
        assert run.status == PipelineStatus.COMPLETED
        assert run.celery_task_id is None

    def test_missing_run_rejected(self, test_db):
        from backend.tasks.streaming_pipeline import _claim_run
        reason = _claim_run(test_db, "00000000-0000-4000-8000-000000000000", "t")
        assert reason is not None and "not found" in reason.lower()

    def test_task_options_bounded_visibility(self):
        from backend.tasks.streaming_pipeline import run_streaming_pipeline
        assert getattr(run_streaming_pipeline, "acks_late", False) is True
        timeout = getattr(run_streaming_pipeline, "visibility_timeout", None)
        assert timeout is not None and timeout <= 3600, (
            "acks_late daemon must bound its visibility timeout")
        assert getattr(run_streaming_pipeline, "time_limit", None) is None


class TestM044TransientDB:
    """Transient PG errors must not kill the daemon or strand runs (M-044)."""

    def _run(self, db, status):
        import uuid as uuid_module

        from backend.models import PipelineRun
        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="stream-run",
            status=status,
            yaml_config="pipeline:\n  name: s\n  steps: []\n",
        )
        db.add(run)
        db.commit()
        return run

    def _transient(self):
        from sqlalchemy.exc import OperationalError
        return OperationalError(
            "SELECT 1", {}, Exception("connection reset by peer"))

    def _flaky_db(self, real_db, failures=1, exc=None):
        """Wrap a session so the first `failures` queries raise transiently."""
        from unittest.mock import patch

        real_query = real_db.query
        state = {"n": 0}
        boom = exc or self._transient()

        def flaky(*args, **kwargs):
            state["n"] += 1
            if state["n"] <= failures:
                raise boom
            return real_query(*args, **kwargs)

        return patch.object(real_db, "query", side_effect=flaky)

    def test_is_aborted_transient_error_keeps_running(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _is_aborted
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE)
        with self._flaky_db(test_db):
            assert _is_aborted(test_db, run.id) is False
        # Session must be usable afterwards (rolled back, not poisoned).
        assert _is_aborted(test_db, run.id) is False

    def test_is_aborted_terminal_and_missing_stop(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _is_aborted
        run = self._run(test_db, PipelineStatus.FAILED)
        assert _is_aborted(test_db, run.id) is True
        assert _is_aborted(test_db, run.id) is True  # missing run

    def test_handle_pause_survives_transient_error(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks.streaming_pipeline import _handle_pause
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE)
        # Status read itself flaps transiently: pause check must return
        # False (keep running) rather than stopping the daemon.
        with self._flaky_db(test_db):
            assert _handle_pause(test_db, run.id) is False

    def test_pause_resume_returns_without_stopping(self, test_db):
        """PAUSED -> ACTIVE while waiting returns False (M-152: cancellable)."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        statuses = [
            PipelineStatus.STREAMING_PAUSED,   # entry read
            PipelineStatus.STREAMING_PAUSED,   # abort poll
            PipelineStatus.STREAMING_ACTIVE,   # re-read -> resume
            PipelineStatus.STREAMING_ACTIVE,   # final abort check
        ]

        class ScriptDb:
            def __init__(self):
                self.calls = 0

            def expire_all(self):
                pass

            def rollback(self):
                pass

            def query(self, *args):
                return self

            def filter(self, *args):
                return self

            def first(self):
                idx = min(self.calls, len(statuses) - 1)
                self.calls += 1
                return SimpleNamespace(status=statuses[idx])

        with patch.object(sp.time, "sleep", lambda s: None):
            assert sp._handle_pause(ScriptDb(), "run-id") is False

    def test_pause_stop_returns_true(self):
        """PAUSED -> STOPPED while waiting returns True (stop wins)."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        statuses = [
            PipelineStatus.STREAMING_PAUSED,
            PipelineStatus.STREAMING_PAUSED,
            PipelineStatus.STREAMING_STOPPED,
            PipelineStatus.STREAMING_STOPPED,
        ]

        class ScriptDb:
            def __init__(self):
                self.calls = 0

            def expire_all(self):
                pass

            def rollback(self):
                pass

            def query(self, *args):
                return self

            def filter(self, *args):
                return self

            def first(self):
                idx = min(self.calls, len(statuses) - 1)
                self.calls += 1
                return SimpleNamespace(status=statuses[idx])

        with patch.object(sp.time, "sleep", lambda s: None):
            assert sp._handle_pause(ScriptDb(), "run-id") is True


class TestM078Transitions:
    """Daemon-owned pause/resume/stop/restart state machine (M-078).

    Sibling routes call request_pause/resume/stop/restart(run_id, user)
    and map (ok, error) to HTTP codes. All transitions are atomic
    check-and-set; races surface as conflicts, never silent toggles.
    """

    def _user(self, role="viewer"):
        import uuid as uuid_module
        from types import SimpleNamespace
        return SimpleNamespace(id=uuid_module.uuid4(), role=role)

    def _run(self, db, status, user_id=None):
        import uuid as uuid_module

        from backend.models import PipelineRun
        run = PipelineRun(
            id=uuid_module.uuid4(),
            name="stream-run",
            status=status,
            yaml_config="pipeline:\n  name: s\n  steps: []\n",
            user_id=user_id,
        )
        db.add(run)
        db.commit()
        return run

    def test_pause_active_to_paused(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, user.id)
        ok, error = sp.request_pause(str(run.id), user, test_db)
        assert (ok, error) == (True, None)
        assert run.status == PipelineStatus.STREAMING_PAUSED

    def test_pause_wrong_state_conflicts(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_STOPPED, user.id)
        ok, error = sp.request_pause(str(run.id), user, test_db)
        assert ok is False
        assert "conflict" in error
        assert run.status == PipelineStatus.STREAMING_STOPPED

    def test_resume_paused_to_active(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_PAUSED, user.id)
        ok, error = sp.request_resume(str(run.id), user, test_db)
        assert (ok, error) == (True, None)
        assert run.status == PipelineStatus.STREAMING_ACTIVE

    def test_resume_active_conflicts(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, user.id)
        ok, error = sp.request_resume(str(run.id), user, test_db)
        assert ok is False
        assert "conflict" in error

    def test_double_pause_second_conflicts(self, test_db):
        """Concurrent pause/pause must not toggle: loser gets conflict."""
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, user.id)
        assert sp.request_pause(str(run.id), user, test_db) == (True, None)
        ok, error = sp.request_pause(str(run.id), user, test_db)
        assert ok is False
        assert "conflict" in error
        assert run.status == PipelineStatus.STREAMING_PAUSED

    def test_stop_from_active_and_paused(self, test_db):
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        for start in (
            PipelineStatus.STREAMING_ACTIVE, PipelineStatus.STREAMING_PAUSED,
        ):
            run = self._run(test_db, start, user.id)
            with patch.object(sp, "celery_app") as mock_app:
                ok, error = sp.request_stop(str(run.id), user, test_db)
            assert (ok, error) == (True, None), error
            assert run.status == PipelineStatus.STREAMING_STOPPED
            mock_app.control.revoke.assert_called_once()

    def test_stop_wrong_state_no_revoke(self, test_db):
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_STOPPED, user.id)
        with patch.object(sp, "celery_app") as mock_app:
            ok, error = sp.request_stop(str(run.id), user, test_db)
        assert ok is False
        assert "conflict" in error
        mock_app.control.revoke.assert_not_called()

    def test_restart_stopped_dispatches_new_task(self, test_db):
        from types import SimpleNamespace
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_STOPPED, user.id)
        with (
            patch.object(sp, "celery_app"),
            patch.object(
                sp.run_streaming_pipeline, "delay",
                return_value=SimpleNamespace(id="task-new"),
            ) as mock_delay,
        ):
            ok, error = sp.request_restart(str(run.id), user, test_db)
        assert (ok, error) == (True, None), error
        mock_delay.assert_called_once_with(str(run.id))
        assert run.status == PipelineStatus.STREAMING_ACTIVE
        assert run.celery_task_id == "task-new"

    def test_restart_active_conflicts_without_dispatch(self, test_db):
        from unittest.mock import patch

        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, user.id)
        with (
            patch.object(sp, "celery_app"),
            patch.object(
                sp.run_streaming_pipeline, "delay") as mock_delay,
        ):
            ok, error = sp.request_restart(str(run.id), user, test_db)
        assert ok is False
        assert "conflict" in error
        mock_delay.assert_not_called()
        assert run.status == PipelineStatus.STREAMING_ACTIVE

    def test_non_owner_denied_as_not_found(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        owner = self._user()
        stranger = self._user()
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, owner.id)
        ok, error = sp.request_pause(str(run.id), stranger, test_db)
        assert ok is False
        assert "not found" in error

    def test_admin_may_transition_foreign_run(self, test_db):
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        owner = self._user()
        admin = self._user(role="admin")
        run = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, owner.id)
        ok, error = sp.request_pause(str(run.id), admin, test_db)
        assert (ok, error) == (True, None), error

    def test_unknown_and_malformed_ids_are_not_found(self, test_db):
        import uuid as uuid_module

        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        ok, error = sp.request_pause(str(uuid_module.uuid4()), user, test_db)
        assert ok is False
        assert "not found" in error
        ok, error = sp.request_stop("not-a-uuid", user, test_db)
        assert ok is False
        assert "not found" in error

    def test_final_settle_preserves_terminal_status(self, test_db):
        """Loop exit must not clobber FAILED/CANCELLED with STOPPED."""
        from backend.models import PipelineStatus
        from backend.tasks import streaming_pipeline as sp

        user = self._user()
        failed = self._run(test_db, PipelineStatus.FAILED, user.id)
        assert sp._settle_final_status(test_db, failed.id) == PipelineStatus.FAILED
        assert failed.status == PipelineStatus.FAILED
        cancelled = self._run(test_db, PipelineStatus.CANCELLED, user.id)
        assert sp._settle_final_status(
            test_db, cancelled.id) == PipelineStatus.CANCELLED
        active = self._run(test_db, PipelineStatus.STREAMING_ACTIVE, user.id)
        assert sp._settle_final_status(
            test_db, active.id) == PipelineStatus.STREAMING_STOPPED
        assert active.status == PipelineStatus.STREAMING_STOPPED
