"""Tests for truthful webhook/notification delivery task reporting."""

from backend.services.notification_service import notify_pipeline_event
from backend.services.webhook_service import trigger_webhooks_for_run


def test_notify_pipeline_event_reports_skipped_when_nothing_matches(test_db):
    report = notify_pipeline_event(
        db=test_db,
        event_type="pipeline_completed",
        pipeline_name="demo",
        run_id="run-1",
    )

    assert report["status"] == "skipped"
    assert report["matched_configs"] == 0
    assert report["sent"] == 0


def test_trigger_webhooks_for_run_reports_skipped_when_nothing_matches(monkeypatch):
    class _EmptyQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    class _EmptySession:
        def query(self, *args, **kwargs):
            return _EmptyQuery()

        def close(self):
            return None

    monkeypatch.setattr("backend.services.webhook_service.SessionLocal", lambda: _EmptySession())

    report = trigger_webhooks_for_run(
        run_id="run-1",
        status="COMPLETED",
        pipeline_name="demo",
    )

    assert report["status"] == "skipped"
    assert report["matched_webhooks"] == 0
    assert report["delivered"] == 0
