"""Regression tests for M-116: outage-resilience machinery must be wired.

Locks in that every fallback/fan-out entry point owned by this wave is
referenced by a live caller — i.e. nothing here is dead code:

* ``fire_webhooks_for_event`` is the webhook_service fan-out path and
  enqueues only webhooks subscribed to the event (inactive ones stay
  silent, including archived webhooks from M-026);
* ``deliver_webhooks_task`` is the pipeline_tasks fan-out path and both
  delivery tasks are registered on their queues;
* ``reconcile_stuck_runs_task`` is on the Celery beat schedule.

The Redpanda Redis fallback queue (``try_drain_fallback``) is likewise
wired — it is drained from ``streaming_pipeline`` (Wave-1 daemon
territory), so there is nothing to delete; see REPORT.md.
"""

from unittest.mock import patch

from backend.tasks import webhook_tasks
from backend.tasks.webhook_tasks import fire_webhooks_for_event


def test_fire_webhooks_skips_empty_user():
    """Empty user_id returns immediately without touching the broker."""
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        fire_webhooks_for_event("pipeline_completed", {"a": 1}, "")
    mock_enqueue.assert_not_called()


def test_fire_webhooks_enqueues_only_subscribed(test_db, monkeypatch):
    """Only active webhooks subscribed to the event are enqueued."""
    import uuid

    from backend.models import User, Webhook

    user = User(
        id=uuid.uuid4(), email="m116@t.local", username="m116",
        hashed_password="x", role="viewer", is_active=True)
    test_db.add(user)
    test_db.flush()
    subscribed = Webhook(
        user_id=user.id, url="https://example.com/sub",
        events=["pipeline_completed"])
    unsubscribed = Webhook(
        user_id=user.id, url="https://example.com/other",
        events=["pipeline_failed"])
    inactive = Webhook(
        user_id=user.id, url="https://example.com/off",
        events=["pipeline_completed"], is_active=False)
    test_db.add_all([subscribed, unsubscribed, inactive])
    test_db.commit()

    monkeypatch.setattr(
        "backend.database.SessionLocal", lambda: test_db)
    with patch(
        "backend.tasks.webhook_tasks.deliver_webhook.apply_async"
    ) as mock_enqueue:
        fire_webhooks_for_event(
            "pipeline_completed", {"data": {}}, str(user.id))

    assert mock_enqueue.call_count == 1
    enqueued = mock_enqueue.call_args
    assert enqueued.kwargs["queue"] == "critical"
    assert enqueued.kwargs["args"][0] == str(subscribed.id)


def test_delivery_tasks_registered_on_queues():
    """Both delivery tasks exist in the Celery registry (not dead)."""
    from backend.celery_app import celery_app

    for name in ("tasks.deliver_webhook", "webhooks.deliver"):
        assert name in celery_app.tasks, f"{name} not registered"
        task = celery_app.tasks[name]
        assert task.acks_late is True


def test_reconciler_on_beat_schedule():
    """The stuck-run reconciler is scheduled (single-flight, M-018)."""
    from backend import celery_config

    scheduled = [
        entry.get("task") for entry in celery_config.beat_schedule.values()
    ]
    assert "tasks.reconcile_stuck_runs" in scheduled
    assert "tasks.reconcile_stuck_runs" in celery_config.task_routes
    assert webhook_tasks.deliver_webhook.name == "tasks.deliver_webhook"
