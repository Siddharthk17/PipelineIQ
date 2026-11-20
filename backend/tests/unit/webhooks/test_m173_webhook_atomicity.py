"""Regression tests for M-173 (webhooks): a failed commit must roll the
session back so later requests on the same session still work.

The injected failure fires AFTER flush (the statement reached the
database), which is what leaves a session failed/poisoned without an
explicit rollback. TestClient re-raises server exceptions, so failed
requests assert ``pytest.raises``; the discriminator is what the NEXT
request observes.
"""

import uuid

import pytest

from backend.models import WebhookDelivery


def _register_and_login(auth_client, tag):
    auth_client.post("/auth/register", json={
        "email": f"m173_{tag}@t.local",
        "username": f"m173_{tag}",
        "password": "Testpass123!",
    })
    resp = auth_client.post("/auth/login", json={
        "email": f"m173_{tag}@t.local",
        "password": "Testpass123!",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _fail_next_commit_after_flush(test_db, monkeypatch):
    """Fail the next commit after its statements have flushed."""
    orig_commit = test_db.commit
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            test_db.flush()  # statements sent; transaction now open
            raise RuntimeError("commit boom")
        return orig_commit(*args, **kwargs)

    monkeypatch.setattr(test_db, "commit", flaky)


def test_create_recovers_after_commit_failure(auth_client, test_db, monkeypatch):
    """A failed create leaves no phantom row; the next create succeeds."""
    headers = _register_and_login(auth_client, "atomic")
    _fail_next_commit_after_flush(test_db, monkeypatch)

    with pytest.raises(RuntimeError, match="commit boom"):
        auth_client.post("/webhooks/", json={
            "url": "https://example.com/atomic"}, headers=headers)

    second = auth_client.post("/webhooks/", json={
        "url": "https://example.com/atomic2"}, headers=headers)
    assert second.status_code == 201, second.text

    # Without the rollback the flushed INSERT would survive into the next
    # transaction as a phantom duplicate.
    listed = auth_client.get("/webhooks/", headers=headers).json()
    assert len(listed) == 1


def test_delete_recovers_after_commit_failure(auth_client, test_db, monkeypatch):
    """A failed delete rolls back; the retry still finds and deletes."""
    headers = _register_and_login(auth_client, "atomicdel")
    create = auth_client.post("/webhooks/", json={
        "url": "https://example.com/atomicdel"}, headers=headers)
    webhook_id = create.json()["id"]

    _fail_next_commit_after_flush(test_db, monkeypatch)
    with pytest.raises(RuntimeError, match="commit boom"):
        auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)

    # Without the rollback the flushed DELETE would hide the row from the
    # retry (404) even though nothing committed.
    second = auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)
    assert second.status_code == 204

    listed = auth_client.get("/webhooks/", headers=headers).json()
    assert [w for w in listed if w["id"] == webhook_id] == []


def test_archived_delete_recovers_after_commit_failure(
    auth_client, test_db, monkeypatch,
):
    """A failed archive rolls back; the webhook stays active, retry works."""
    headers = _register_and_login(auth_client, "atomicarch")
    create = auth_client.post("/webhooks/", json={
        "url": "https://example.com/atomicarch"}, headers=headers)
    webhook_id = create.json()["id"]
    test_db.add(WebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=uuid.UUID(webhook_id),
        event_type="pipeline_completed",
        payload={"event": "x"},
        attempt_number=1,
    ))
    test_db.commit()

    _fail_next_commit_after_flush(test_db, monkeypatch)
    with pytest.raises(RuntimeError, match="commit boom"):
        auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)

    # Archive rolled back: still active, history intact; retry archives.
    listed = auth_client.get("/webhooks/", headers=headers).json()
    row = [w for w in listed if w["id"] == webhook_id]
    assert len(row) == 1 and row[0]["is_active"] is True

    assert auth_client.delete(
        f"/webhooks/{webhook_id}", headers=headers).status_code == 204
