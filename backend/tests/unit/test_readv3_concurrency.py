"""Regression tests for readversarial-3 concurrency findings G/H/I/J.

G: pipelines' forked idempotency waiter never expired the cached row, so a
   truly-concurrent duplicate run-create always timed out to 409 instead of
   replaying the owner's 202. The fork is deleted; the run paths delegate to
   the contracts helper (which expires before re-reading).
H: idempotency placeholders were pinned forever on non-HTTP errors
   (contracts released only on HTTPException; schedules/webhooks never
   released) and never expired. Claim now releases on any error and GCs
   stale placeholders (status NULL older than 24h) at claim time.
I: resume stamped its dispatched task id unconditionally, so a concurrent
   stop's revoke missed the live daemon and the run resurrected. The stamp
   is now guarded on ACTIVE (orphan revoked on loss) and stop revokes the
   post-transition task id.
J: streaming _fail_run / the daemon generic-except set FAILED
   unconditionally, clobbering a concurrent CANCELLED. Both are now
   conditional (mirroring batch _mark_failed).

All tests run on the hermetic SQLite test DB; no live services needed.
"""
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

import backend.api.contracts as contracts_mod
import backend.api.pipelines as pipelines_mod
import backend.tasks.streaming_pipeline as sp
from backend.models import PipelineRun, PipelineStatus
from backend.models.data_contract import IdempotencyKey


def _user_id():
    return uuid.uuid4()


def _make_user(db, role="admin"):
    from backend.models import User

    user = User(
        id=uuid.uuid4(),
        email=f"rc3_{uuid.uuid4().hex[:8]}@t.local",
        username=f"rc3_{uuid.uuid4().hex[:8]}",
        hashed_password="x",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    return user


def _other_session(db):
    return sessionmaker(bind=db.get_bind())()


# ---------------------------------------------------------------------------
# G: waiter sees a concurrent owner's completion.
# ---------------------------------------------------------------------------


class TestGSharedWaiter:
    def test_run_paths_unified_on_contracts_waiter(self, test_db, monkeypatch):
        # The fork is gone: the run-create paths must delegate to the
        # contracts helper that expires the cached row before re-reading.
        assert not hasattr(pipelines_mod, "_idempotency_wait"), (
            "pipelines must not carry its own _idempotency_wait fork; "
            "run-create paths must use backend.api.contracts._idempotency_wait"
        )

    def test_waiter_sees_concurrent_completion(self, test_db, monkeypatch):
        monkeypatch.setattr(
            contracts_mod, "_IDEMPOTENT_INFLIGHT_TIMEOUT_S", 3.0)
        monkeypatch.setattr(
            contracts_mod, "_IDEMPOTENT_POLL_INTERVAL_S", 0.05)
        user_id = _user_id()

        # Owner claims the key.
        action, owner_row = pipelines_mod._idempotency_claim(
            test_db, scope="pipeline_run", key="g-key",
            user_id=user_id,
        )
        assert action == "owner"

        # Duplicate arrives while the owner is still working: same session
        # caches the placeholder (status None), exactly like the run
        # handlers' "inflight" branch.
        other = _other_session(test_db)
        try:
            action2, dup_row = pipelines_mod._idempotency_claim(
                other, scope="pipeline_run", key="g-key",
                user_id=user_id,
            )
            assert action2 == "inflight"

            # Owner finishes concurrently through its own session.
            contracts_mod._idempotency_complete(
                test_db, owner_row, status_code=202,
                body={"run_id": "r1"}, resource_id="r1",
            )

            # The waiter the run-create paths use must see the completion
            # instead of timing out to a spurious 409.
            waiter = getattr(pipelines_mod, "_idempotency_wait", None)
            if waiter is None:
                waiter = contracts_mod._idempotency_wait
            done = waiter(other, dup_row)
            assert done is not None, (
                "waiter never saw the concurrent completion (would 409)")
            assert done.status_code == 202
            assert done.response_body == {"run_id": "r1"}
        finally:
            other.close()


# ---------------------------------------------------------------------------
# H: release-on-error + stale-placeholder GC.
# ---------------------------------------------------------------------------


def _backdate_placeholder(db, row, age):
    row.created_at = datetime.now(UTC).replace(tzinfo=None) - age
    db.add(row)
    db.commit()


class TestHStalePlaceholderGC:
    def test_stale_placeholder_reclaimed_at_claim(self, test_db):
        user_id = _user_id()
        stale = IdempotencyKey(
            scope="contract_create", key="h-stale", user_id=user_id,
            status_code=None, response_body=None,
        )
        test_db.add(stale)
        test_db.commit()
        _backdate_placeholder(test_db, stale, timedelta(hours=25))

        action, row = contracts_mod._idempotency_claim(
            test_db, scope="contract_create", key="h-stale",
            user_id=user_id,
        )
        assert action == "owner", (
            f"stale placeholder must be reclaimed, got {action}")
        rows = test_db.query(IdempotencyKey).filter(
            IdempotencyKey.scope == "contract_create",
            IdempotencyKey.key == "h-stale").all()
        assert len(rows) == 1 and rows[0].id == row.id

    def test_fresh_placeholder_still_inflight(self, test_db):
        user_id = _user_id()
        action, _ = contracts_mod._idempotency_claim(
            test_db, scope="contract_create", key="h-fresh",
            user_id=user_id,
        )
        assert action == "owner"
        action2, _ = contracts_mod._idempotency_claim(
            test_db, scope="contract_create", key="h-fresh",
            user_id=user_id,
        )
        assert action2 == "inflight"

    def test_stale_sweep_cleans_other_keys(self, test_db):
        user_id = _user_id()
        for k in ("h-sweep-1", "h-sweep-2"):
            row = IdempotencyKey(
                scope="schedule_create", key=k, user_id=user_id,
                status_code=None, response_body=None,
            )
            test_db.add(row)
        test_db.commit()
        for k in ("h-sweep-1", "h-sweep-2"):
            row = test_db.query(IdempotencyKey).filter(
                IdempotencyKey.key == k).one()
            _backdate_placeholder(test_db, row, timedelta(hours=30))
        # Claiming an unrelated key sweeps the stale ones in the scope.
        action, _ = contracts_mod._idempotency_claim(
            test_db, scope="schedule_create", key="h-sweep-new",
            user_id=user_id,
        )
        assert action == "owner"
        leftover = test_db.query(IdempotencyKey).filter(
            IdempotencyKey.scope == "schedule_create",
            IdempotencyKey.key.in_(["h-sweep-1", "h-sweep-2"])).count()
        assert leftover == 0

    def test_pipeline_claim_reclaims_stale(self, test_db):
        user_id = _user_id()
        stale = IdempotencyKey(
            scope="pipeline_run", key="h-pipe-stale", user_id=user_id,
            status_code=None, response_body=None,
        )
        test_db.add(stale)
        test_db.commit()
        _backdate_placeholder(test_db, stale, timedelta(hours=25))
        action, _ = pipelines_mod._idempotency_claim(
            test_db, scope="pipeline_run", key="h-pipe-stale",
            user_id=user_id,
        )
        assert action == "owner"


class TestHReleaseOnError:
    def test_contract_create_db_blip_frees_key(self, test_db, monkeypatch):
        from backend.schemas import ContractCreateRequest

        admin = _make_user(test_db, role="admin")
        body = ContractCreateRequest(
            yaml_content=(
                "columns:\n"
                "  order_id:\n"
                "    type: integer\n"
                "    nullable: false\n"
            ),
            severity="warn",
            consumers=[],
        )
        request = SimpleNamespace(headers={"idempotency-key": "h-blip"})

        def _blip(*args, **kwargs):
            raise RuntimeError("simulated DB blip mid-commit")

        monkeypatch.setattr(
            contracts_mod, "_create_contract_version", _blip)
        with pytest.raises(RuntimeError):
            contracts_mod.create_contract(
                "h_pipe", body, request, test_db, admin)
        # Retry with the same key must get a fresh attempt, not a
        # permanent 409 "still in progress".
        action, _ = contracts_mod._idempotency_claim(
            test_db, scope="contract_create", key="h-blip",
            user_id=admin.id,
        )
        assert action == "owner", (
            f"key still pinned after failed create (got {action})")

    def test_schedule_create_failure_frees_key(self, test_db, monkeypatch):
        import backend.api.schedules as schedules_mod
        from backend.models import UploadedFile

        admin = _make_user(test_db, role="admin")
        seed = UploadedFile(
            original_filename="h.csv",
            stored_path="/tmp/h.csv",
            file_size_bytes=10,
            row_count=1,
            column_count=1,
            columns=["a"],
            dtypes={"a": "int64"},
            user_id=admin.id,
        )
        test_db.add(seed)
        test_db.commit()
        body = schedules_mod.CreateScheduleRequest(
            pipeline_name="h_sched",
            yaml_config=(
                "pipeline:\n  name: h_sched\n  steps:\n"
                "    - name: load_it\n"
                "      type: load\n"
                f"      file_id: {seed.id}\n"
            ),
            cron_expression="0 3 * * *",
        )
        request = SimpleNamespace(headers={"idempotency-key": "h-sched"})

        def _blip(*args, **kwargs):
            raise RuntimeError("simulated audit outage")

        monkeypatch.setattr(schedules_mod, "log_action", _blip)
        with pytest.raises(RuntimeError):
            schedules_mod.create_schedule(request, body, test_db, admin)
        action, _ = contracts_mod._idempotency_claim(
            test_db, scope="schedule_create", key="h-sched",
            user_id=admin.id,
        )
        assert action == "owner", (
            f"key still pinned after failed schedule create (got {action})")

    def test_webhook_create_failure_frees_key(self, test_db, monkeypatch):
        import backend.api.webhooks as webhooks_mod

        admin = _make_user(test_db, role="admin")
        body = webhooks_mod.WebhookCreate.model_construct(
            url="https://example.com/hook", secret=None, events=None)
        request = SimpleNamespace(headers={"idempotency-key": "h-wh"})

        def _blip(*args, **kwargs):
            raise RuntimeError("simulated audit outage")

        monkeypatch.setattr(webhooks_mod, "log_action", _blip)
        with pytest.raises(RuntimeError):
            webhooks_mod.create_webhook(body, request, test_db, admin)
        action, _ = contracts_mod._idempotency_claim(
            test_db, scope="webhook_create", key="h-wh",
            user_id=admin.id,
        )
        assert action == "owner", (
            f"key still pinned after failed webhook create (got {action})")


# ---------------------------------------------------------------------------
# I: resume/stop race.
# ---------------------------------------------------------------------------


def _seed_stream_run(db, status, user_id, task_id=None):
    run = PipelineRun(
        id=uuid.uuid4(),
        name=f"i_{uuid.uuid4().hex[:8]}",
        status=status,
        yaml_config="pipeline:\n  name: i\n  steps: []\n",
        user_id=user_id,
        celery_task_id=task_id,
    )
    db.add(run)
    db.commit()
    return run


class TestIResumeStopRace:
    def test_resume_then_stop_leaves_stopped_without_live_daemon(
        self, test_db, monkeypatch,
    ):
        admin = _make_user(test_db, role="admin")
        user = SimpleNamespace(id=admin.id, role="admin")
        run = _seed_stream_run(
            test_db, PipelineStatus.STREAMING_PAUSED, admin.id)
        revoked = []
        fake_app = MagicMock()
        fake_app.control.revoke.side_effect = lambda *a, **k: revoked.append(
            a[0] if a else None)
        monkeypatch.setattr(sp, "celery_app", fake_app)

        other = _other_session(test_db)
        try:
            def fake_delay(run_id_str):
                # Stop wins between resume's dispatch and its task-id
                # stamp (seconds-long window in production).
                ok, err = sp.request_stop(run_id_str, user, db=other)
                assert ok, err
                return SimpleNamespace(id="D1")

            fake_task = MagicMock()
            fake_task.delay.side_effect = fake_delay
            monkeypatch.setattr(sp, "run_streaming_pipeline", fake_task)

            ok, err = sp.request_resume(str(run.id), user, db=test_db)
            assert not ok, "resume must lose to the concurrent stop"
        finally:
            other.close()

        test_db.expire_all()
        final = test_db.query(PipelineRun).filter(
            PipelineRun.id == run.id).one()
        assert final.status == PipelineStatus.STREAMING_STOPPED
        assert final.celery_task_id != "D1", (
            "resume stamped its daemon onto a STOPPED run: D1 would boot "
            "into a stopped run with nothing revoking it")
        assert "D1" in revoked, "orphaned resume daemon was never revoked"

    def test_stop_revokes_post_transition_task_id(
        self, test_db, monkeypatch,
    ):
        admin = _make_user(test_db, role="admin")
        user = SimpleNamespace(id=admin.id, role="admin")
        run = _seed_stream_run(
            test_db, PipelineStatus.STREAMING_ACTIVE, admin.id,
            task_id="T-old",
        )
        revoked = []
        fake_app = MagicMock()
        fake_app.control.revoke.side_effect = lambda *a, **k: revoked.append(
            a[0] if a else None)
        monkeypatch.setattr(sp, "celery_app", fake_app)

        real_atomic = sp._atomic_transition

        def _stamp_then_win(db, run_uuid, expected, to_status, extra=None):
            won = real_atomic(db, run_uuid, expected, to_status, extra)
            if won:
                # A concurrent resume stamps a fresh daemon id after our
                # claim but before our revoke.
                db.query(PipelineRun).filter(
                    PipelineRun.id == run_uuid).update(
                        {"celery_task_id": "T-new"})
                db.commit()
            return won

        monkeypatch.setattr(sp, "_atomic_transition", _stamp_then_win)
        ok, err = sp.request_stop(str(run.id), user, db=test_db)
        assert ok, err
        assert revoked == ["T-new"], (
            f"stop revoked a stale task id {revoked}; the live daemon "
            "would survive")


# ---------------------------------------------------------------------------
# J: never clobber CANCELLED.
# ---------------------------------------------------------------------------


class TestJNeverClobberCancelled:
    def test_fail_run_keeps_cancelled(self, test_db):
        admin = _make_user(test_db, role="admin")
        run = _seed_stream_run(
            test_db, PipelineStatus.STREAMING_ACTIVE, admin.id)

        sp._fail_run(test_db, run.id, "boom")
        test_db.expire_all()
        assert test_db.query(PipelineRun).filter(
            PipelineRun.id == run.id).one().status == PipelineStatus.FAILED

        # A cancel lands after the daemon's last read, before its write.
        other = _other_session(test_db)
        try:
            victim = other.query(PipelineRun).filter(
                PipelineRun.id == run.id).one()
            victim.status = PipelineStatus.CANCELLED
            other.commit()
        finally:
            other.close()

        sp._fail_run(test_db, run.id, "late boom after cancel")
        test_db.expire_all()
        final = test_db.query(PipelineRun).filter(
            PipelineRun.id == run.id).one()
        assert final.status == PipelineStatus.CANCELLED, (
            f"concurrent cancel was clobbered to {final.status}")

    def test_crashing_daemon_keeps_cancelled(self, monkeypatch):
        # Full daemon path with str run ids (as Celery delivers them), so
        # this needs the throwaway PG: SQLite's UUID binding rejects str.
        from sqlalchemy import create_engine

        from backend.database import Base

        pg_url = ("postgresql://postgres:pipelineiq_dev_password"
                  "@localhost:5432/pipelineiq")
        try:
            eng = create_engine(pg_url)
            with eng.connect():
                pass
        except Exception:
            pytest.skip("throwaway PG unavailable")
        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng)
        db = factory()
        try:
            from backend.models import User

            admin = User(
                id=uuid.uuid4(),
                email=f"rc3j_{uuid.uuid4().hex[:8]}@t.local",
                username=f"rc3j_{uuid.uuid4().hex[:8]}",
                hashed_password="x",
                role="admin",
                is_active=True,
            )
            db.add(admin)
            db.commit()
            run = _seed_stream_run(
                db, PipelineStatus.STREAMING_ACTIVE, admin.id)
            run_id = str(run.id)
        finally:
            db.close()

        monkeypatch.setattr(sp, "SessionLocal", factory)

        consume = MagicMock()
        consume.type = "stream_consume"
        consume.name = "ingest"
        consume.topic = "t"
        consume.consumer_group = None
        consume.batch_size = 1
        consume.batch_timeout_ms = 100
        consume.deserialize = "json"
        monkeypatch.setattr(
            sp, "get_parsed_pipeline",
            lambda yaml_config: SimpleNamespace(steps=[consume]))
        monkeypatch.setattr(
            sp, "get_admin_client",
            lambda: MagicMock(ensure_topic=lambda topic: None))
        monkeypatch.setattr(sp, "make_dlq_producer", lambda: MagicMock())

        def _subscribe(topics):
            canceller = factory()
            try:
                victim = canceller.query(PipelineRun).filter(
                    PipelineRun.id == uuid.UUID(run_id)).one()
                victim.status = PipelineStatus.CANCELLED
                canceller.commit()
            finally:
                canceller.close()
            raise RuntimeError("kafka down")

        consumer = MagicMock()
        consumer.subscribe.side_effect = _subscribe
        monkeypatch.setattr(sp, "_daemon_consumer", lambda *a, **k: consumer)

        with pytest.raises(RuntimeError):
            sp.run_streaming_pipeline.apply(args=[run_id]).get()

        check = factory()
        try:
            final = check.query(PipelineRun).filter(
                PipelineRun.id == uuid.UUID(run_id)).one().status
        finally:
            check.close()
        assert final == PipelineStatus.CANCELLED, (
            f"crashing daemon clobbered a concurrent cancel to {final}")
