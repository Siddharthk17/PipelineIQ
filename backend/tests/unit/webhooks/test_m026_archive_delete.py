"""Regression tests for M-026 (+M-081 webhook part).

Deleting a webhook that has delivery history must ARCHIVE the webhook
(``is_active=False``) and keep the history — not fail with 500 (the PG
append-only RULE on ``webhook_deliveries`` turns the cascade into an
FK violation) and not silently cascade-delete the audit trail.
"""

import uuid

from backend.models import WebhookDelivery


def _register_and_login(auth_client, tag):
    auth_client.post("/auth/register", json={
        "email": f"m026_{tag}@t.local",
        "username": f"m026_{tag}",
        "password": "Testpass123!",
    })
    resp = auth_client.post("/auth/login", json={
        "email": f"m026_{tag}@t.local",
        "password": "Testpass123!",
    })
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _add_delivery(test_db, webhook_id):
    delivery = WebhookDelivery(
        id=uuid.uuid4(),
        webhook_id=webhook_id,
        event_type="pipeline_completed",
        payload={"event": "x"},
        attempt_number=1,
        response_status=200,
        delivered_at=None,
    )
    test_db.add(delivery)
    test_db.commit()
    return delivery


def test_delete_webhook_with_history_archives(auth_client, test_db):
    """DELETE with delivery history: 204, row archived, history kept."""
    headers = _register_and_login(auth_client, "hist")
    create = auth_client.post("/webhooks/", json={
        "url": "https://history.com/hook"}, headers=headers)
    assert create.status_code == 201
    webhook_id = create.json()["id"]

    _add_delivery(test_db, uuid.UUID(webhook_id))

    resp = auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)
    assert resp.status_code == 204

    # Webhook row retained as archived, delivery history intact.
    listed = auth_client.get("/webhooks/", headers=headers).json()
    archived = [w for w in listed if w["id"] == webhook_id]
    assert len(archived) == 1
    assert archived[0]["is_active"] is False

    deliveries = auth_client.get(
        f"/webhooks/{webhook_id}/deliveries", headers=headers)
    assert deliveries.status_code == 200
    assert len(deliveries.json()) == 1


def test_delete_webhook_without_history_removes_row(auth_client, test_db):
    """DELETE with no history keeps the old hard-delete behaviour."""
    headers = _register_and_login(auth_client, "bare")
    create = auth_client.post("/webhooks/", json={
        "url": "https://bare.com/hook"}, headers=headers)
    webhook_id = create.json()["id"]

    resp = auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)
    assert resp.status_code == 204

    listed = auth_client.get("/webhooks/", headers=headers).json()
    assert [w for w in listed if w["id"] == webhook_id] == []


def test_archived_webhook_frees_cap_slot(auth_client, test_db):
    """Archived webhooks must not consume the 10-active-webhook cap."""
    headers = _register_and_login(auth_client, "cap")
    first = auth_client.post("/webhooks/", json={
        "url": "https://example.com/cap0"}, headers=headers)
    assert first.status_code == 201
    _add_delivery(test_db, uuid.UUID(first.json()["id"]))

    assert auth_client.delete(
        f"/webhooks/{first.json()['id']}", headers=headers).status_code == 204

    for i in range(1, 11):
        resp = auth_client.post("/webhooks/", json={
            "url": f"https://example.com/cap{i}"}, headers=headers)
        assert resp.status_code == 201, resp.text

    # 10 active webhooks now (1 archived aside): the next create is capped.
    capped = auth_client.post("/webhooks/", json={
        "url": "https://example.com/cap-overflow"}, headers=headers)
    assert capped.status_code == 400
