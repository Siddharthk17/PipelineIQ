"""Regression tests for M-162: UUID-vs-string comparisons on SQLite.

Fan-out entry points receive ``user_id`` as ``str`` (callers pass
``str(...)``), but ``Webhook.user_id`` is a ``Uuid`` column. Comparing
the two raises ``AttributeError: 'str' object has no attribute 'hex'``
on SQLite (PostgreSQL coerces the string), so string ids must be
coerced before filtering. Unparseable ids degrade to no-match, never
to an exception.
"""

import uuid
from unittest.mock import patch

from backend.models import User, Webhook
from backend.tasks.webhook_tasks import (
    deliver_webhooks_task,
    fire_webhooks_for_event,
)


def _seed(test_db):
    user = User(
        id=uuid.uuid4(), email="m162@t.local", username="m162",
        hashed_password="x", role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    test_db.add(Webhook(
        user_id=user.id, url="https://example.com/m162",
        events=["pipeline_completed"]))
    test_db.commit()
    return user


def test_fire_webhooks_accepts_string_user_id(test_db, monkeypatch):
    """String user_id must filter correctly on SQLite (not raise)."""
    user = _seed(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        # Callers pass str(user.id); must not raise StatementError.
        fire_webhooks_for_event(
            "pipeline_completed", {"data": {}}, str(user.id))
    assert mock_enqueue.call_count == 1


def test_fire_webhooks_rejects_garbage_user_id(test_db, monkeypatch):
    """Unparseable user_id degrades to no-match, never raises."""
    _seed(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        fire_webhooks_for_event(
            "pipeline_completed", {"data": {}}, "not-a-uuid")
    mock_enqueue.assert_not_called()


def test_fanout_task_accepts_string_user_id(test_db, monkeypatch):
    """deliver_webhooks_task must coerce its string user_id too."""
    user = _seed(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        result = deliver_webhooks_task.apply(
            args=(),
            kwargs={
                "run_id": str(uuid.uuid4()),
                "status": "completed",
                "user_id": str(user.id),
            },
        ).get()
    assert result["matched_webhooks"] == 1
    assert mock_enqueue.call_count == 1


def test_fanout_task_rejects_garbage_user_id(test_db, monkeypatch):
    """deliver_webhooks_task with garbage user_id skips without raising."""
    _seed(test_db)
    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        result = deliver_webhooks_task.apply(
            args=(),
            kwargs={
                "run_id": str(uuid.uuid4()),
                "status": "completed",
                "user_id": "not-a-uuid",
            },
        ).get()
    assert result["matched_webhooks"] == 0
    mock_enqueue.assert_not_called()
