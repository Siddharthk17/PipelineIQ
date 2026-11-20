"""Regression test for R-R-001: fan-out retry must not double-enqueue.

If enqueueing fails partway through the fan-out loop, the Celery retry
must only enqueue the remainder. Previously a bare ``self.retry()``
re-ran the whole loop and delivered duplicates.
"""
import uuid
from unittest.mock import patch

import pytest
from celery import Task

from backend.models import User, Webhook
from backend.tasks.webhook_tasks import deliver_webhooks_task


def _seed_two(test_db):
    user = User(
        id=uuid.uuid4(), email="rr1@t.local", username="r_r001",
        hashed_password="x", role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    ids = []
    for i in range(2):
        wh = Webhook(
            user_id=user.id, url=f"https://example.com/rr1-{i}",
            events=["pipeline_completed"])
        test_db.add(wh)
        test_db.flush()
        ids.append(str(wh.id))
    test_db.commit()
    return user, ids


class _RetrySentinel(Exception):
    def __init__(self, kwargs):
        super().__init__("retry")
        self.retry_kwargs = kwargs


def test_fanout_retry_carries_completed_ids(test_db, monkeypatch):
    """A mid-loop failure must forward already-enqueued ids to the retry."""
    user, ids = _seed_two(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)

    calls = []

    def flaky_enqueue(*, args, **kwargs):
        calls.append(args[0])
        if len(calls) == 2:
            raise ConnectionError("broker hiccup")

    def capture_retry(self, exc=None, **opts):
        raise _RetrySentinel(opts.get("kwargs", {}))

    monkeypatch.setattr(Task, "retry", capture_retry)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async",
        side_effect=flaky_enqueue,
    ):
        with pytest.raises(_RetrySentinel) as exc_info:
            deliver_webhooks_task.apply(
                kwargs={
                    "run_id": str(uuid.uuid4()),
                    "status": "completed",
                    "user_id": str(user.id),
                }
            ).get()
    # First webhook went out, second failed.
    assert calls == ids, calls
    # The retry carries the completed id so it is skipped on redelivery.
    assert exc_info.value.retry_kwargs.get("exclude_ids") == ids[:1]


def test_fanout_honors_exclude_ids(test_db, monkeypatch):
    """A retry with exclude_ids only enqueues the remainder."""
    user, ids = _seed_two(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    recorded = []

    def record_enqueue(*, args, **kwargs):
        recorded.append(args[0])

    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async",
        side_effect=record_enqueue,
    ):
        result = deliver_webhooks_task.apply(
            kwargs={
                "run_id": str(uuid.uuid4()),
                "status": "completed",
                "user_id": str(user.id),
                "exclude_ids": ids[:1],
            }
        ).get()
    assert recorded == ids[1:], recorded
    assert result["matched_webhooks"] == 2
