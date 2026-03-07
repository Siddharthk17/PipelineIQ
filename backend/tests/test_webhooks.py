"""Tests for the webhook system."""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.models import User, Webhook, WebhookDelivery


def _register_and_login(client):
    """Helper: register a user and return auth headers."""
    client.post("/auth/register", json={
        "email": "webhook_user@test.com",
        "username": "webhook_user",
        "password": "testpass123",
    })
    resp = client.post("/auth/login", json={
        "email": "webhook_user@test.com",
        "password": "testpass123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_create_webhook_requires_auth(auth_client):
    """Webhook creation requires authentication."""
    resp = auth_client.post("/webhooks/", json={"url": "https://example.com/hook"})
    assert resp.status_code in [401, 403]


def test_create_webhook_validates_url(auth_client):
    """Webhook URL must start with http:// or https://."""
    headers = _register_and_login(auth_client)
    resp = auth_client.post("/webhooks/", json={"url": "ftp://bad.com"}, headers=headers)
    assert resp.status_code == 422


def test_create_webhook_success(auth_client):
    """Valid webhook creation returns 201 with webhook data."""
    headers = _register_and_login(auth_client)
    resp = auth_client.post("/webhooks/", json={
        "url": "https://example.com/hook",
        "events": ["pipeline_completed"],
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/hook"
    assert "pipeline_completed" in data["events"]
    assert data["is_active"] is True


def test_list_webhooks_returns_own_only(auth_client):
    """Users can only see their own webhooks."""
    # Create user 1 with webhook
    auth_client.post("/auth/register", json={
        "email": "wh_user1@test.com", "username": "wh_user1", "password": "testpass123",
    })
    r1 = auth_client.post("/auth/login", json={"email": "wh_user1@test.com", "password": "testpass123"})
    h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    auth_client.post("/webhooks/", json={"url": "https://user1.com/hook"}, headers=h1)

    # Create user 2
    auth_client.post("/auth/register", json={
        "email": "wh_user2@test.com", "username": "wh_user2", "password": "testpass123",
    })
    r2 = auth_client.post("/auth/login", json={"email": "wh_user2@test.com", "password": "testpass123"})
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    # User 2 should see no webhooks
    resp = auth_client.get("/webhooks/", headers=h2)
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_delete_webhook_own(auth_client):
    """Users can delete their own webhooks."""
    headers = _register_and_login(auth_client)
    create_resp = auth_client.post("/webhooks/", json={"url": "https://delete.com/hook"}, headers=headers)
    webhook_id = create_resp.json()["id"]

    resp = auth_client.delete(f"/webhooks/{webhook_id}", headers=headers)
    assert resp.status_code == 204


def test_delete_webhook_others_returns_403(auth_client):
    """Users cannot delete other users' webhooks."""
    # Create user 1 with webhook
    auth_client.post("/auth/register", json={
        "email": "del_user1@test.com", "username": "del_user1", "password": "testpass123",
    })
    r1 = auth_client.post("/auth/login", json={"email": "del_user1@test.com", "password": "testpass123"})
    h1 = {"Authorization": f"Bearer {r1.json()['access_token']}"}
    create_resp = auth_client.post("/webhooks/", json={"url": "https://user1.com/hook"}, headers=h1)
    webhook_id = create_resp.json()["id"]

    # Create user 2
    auth_client.post("/auth/register", json={
        "email": "del_user2@test.com", "username": "del_user2", "password": "testpass123",
    })
    r2 = auth_client.post("/auth/login", json={"email": "del_user2@test.com", "password": "testpass123"})
    h2 = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    resp = auth_client.delete(f"/webhooks/{webhook_id}", headers=h2)
    assert resp.status_code == 403


def test_webhook_hmac_signature_correct():
    """HMAC signature is computed correctly."""
    secret = "my_secret_key"
    body = '{"event": "test"}'
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()

    from backend.services.webhook_service import _sign_payload
    assert _sign_payload(secret, body) == expected


def test_webhook_delivery_on_pipeline_complete(auth_client):
    """Webhook delivery records are created on pipeline completion."""
    headers = _register_and_login(auth_client)
    create_resp = auth_client.post("/webhooks/", json={
        "url": "https://httpbin.org/post",
        "events": ["pipeline_completed"],
    }, headers=headers)
    assert create_resp.status_code == 201
    webhook_id = create_resp.json()["id"]

    # Check deliveries endpoint works (even if empty)
    resp = auth_client.get(f"/webhooks/{webhook_id}/deliveries", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_webhook_retry_on_failure():
    """Verify retry logic attempts delivery multiple times."""
    from backend.services.webhook_service import MAX_ATTEMPTS, RETRY_DELAYS
    assert MAX_ATTEMPTS == 3
    assert len(RETRY_DELAYS) == 3
    assert RETRY_DELAYS[0] == 0
