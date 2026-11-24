"""Regression tests for readversarial-3 concurrency finding G.

G: pipelines' forked idempotency waiter never expired the cached row, so a
   truly-concurrent duplicate run-create always timed out to 409 instead of
   replaying the owner's 202. The fork is deleted; the run paths delegate to
   the contracts helper (which expires before re-reading).

Runs on the hermetic SQLite test DB; no live services needed.
"""
import uuid

from sqlalchemy.orm import sessionmaker

import backend.api.contracts as contracts_mod
import backend.api.pipelines as pipelines_mod


def _user_id():
    return uuid.uuid4()


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
