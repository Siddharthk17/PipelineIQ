"""Regression tests for notification test-endpoint status codes (B01).

Probing a downstream that does not answer is an expected test outcome,
not a gateway failure. The endpoint must report delivery status with a
200 instead of raising a 502 that reads as our own outage.
"""

import uuid


def _register_login(auth_client):
    suffix = uuid.uuid4().hex[:8]
    auth_client.post(
        "/auth/register",
        json={
            "email": f"b01_{suffix}@t.local",
            "username": f"b01_{suffix}",
            "password": "Testpass123!",
        },
    )
    resp = auth_client.post(
        "/auth/login",
        json={"email": f"b01_{suffix}@t.local", "password": "Testpass123!"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _slack_config(auth_client, headers):
    resp = auth_client.post(
        "/api/v1/notifications/",
        json={
            "type": "slack",
            "config": {"slack_webhook_url": "https://example.com/hooks/x"},
            "events": ["pipeline_completed"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_slack_test_unreachable_reports_status_not_502(
    auth_client, monkeypatch
):
    import backend.api.notifications as notif_mod

    headers = _register_login(auth_client)
    config_id = _slack_config(auth_client, headers)
    monkeypatch.setattr(
        notif_mod, "send_slack_notification", lambda *a, **k: False)

    resp = auth_client.post(
        f"/api/v1/notifications/{config_id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("delivered") is False


def test_slack_test_success_reports_delivered(auth_client, monkeypatch):
    import backend.api.notifications as notif_mod

    headers = _register_login(auth_client)
    config_id = _slack_config(auth_client, headers)
    monkeypatch.setattr(
        notif_mod, "send_slack_notification", lambda *a, **k: True)

    resp = auth_client.post(
        f"/api/v1/notifications/{config_id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("delivered") is True


def test_email_test_unreachable_reports_status_not_502(
    auth_client, monkeypatch
):
    import backend.api.notifications as notif_mod
    from backend.config import settings

    if not settings.SMTP_HOST or not settings.SMTP_FROM:
        # Without SMTP configured the endpoint reports 503 for missing
        # server configuration, which is the correct distinct signal.
        return

    headers = _register_login(auth_client)
    resp = auth_client.post(
        "/api/v1/notifications/",
        json={
            "type": "email",
            "config": {"email_to": "someone@example.com"},
            "events": ["pipeline_completed"],
        },
        headers=headers,
    )
    config_id = resp.json()["id"]
    monkeypatch.setattr(
        notif_mod, "send_email_notification", lambda *a, **k: False)

    resp = auth_client.post(
        f"/api/v1/notifications/{config_id}/test", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("delivered") is False
