"""Coverage for dashboard + audit endpoints (M-127).

These user-facing read paths had no direct tests: admin-only audit listing,
per-user audit scoping, pagination/filter validation, and dashboard stats
shape/auth. All assertions are unconditional and deterministic (SQLite
hermetic DB via fixtures, no sleeps, no live services beyond what
conftest already requires).
"""

from __future__ import annotations

import uuid

import pytest

from backend.models import AuditLog, User

VIEWER_EMAIL = "audit.viewer@t.local"
VIEWER_USERNAME = "audit_viewer"
VIEWER_PASSWORD = "AuditViewer123!"


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def viewer_token(client):
    """Register + log in a viewer; return (token, user_id)."""
    resp = client.post(
        "/auth/register",
        json={
            "email": VIEWER_EMAIL,
            "username": VIEWER_USERNAME,
            "password": VIEWER_PASSWORD,
        },
    )
    assert resp.status_code == 201, resp.text
    login = client.post(
        "/auth/login",
        json={"email": VIEWER_EMAIL, "password": VIEWER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    user_id = login.json()["user"]["id"]
    return login.json()["access_token"], user_id


@pytest.fixture()
def admin_user_id(client, test_db) -> str:
    from backend.tests.conftest import FIXTURE_ADMIN_EMAIL

    user = test_db.query(User).filter(User.email == FIXTURE_ADMIN_EMAIL).first()
    assert user is not None
    return str(user.id)


def _seed_log(test_db, user_id: str | None, action: str) -> None:
    from backend.utils.uuid_utils import as_uuid

    test_db.add(
        AuditLog(
            user_id=as_uuid(user_id) if user_id else None,
            action=action,
            resource_type="pipeline",
            resource_id=uuid.uuid4(),
            details={"k": "v"},
            ip_address="127.0.0.1",
        )
    )
    test_db.commit()


class TestAuditLogsAdmin:
    def test_requires_auth(self, auth_client):
        resp = auth_client.get("/audit/logs")
        assert resp.status_code == 401, resp.text

    def test_viewer_forbidden(self, client, viewer_token):
        token, _ = viewer_token
        resp = client.get("/audit/logs", headers=_auth_header(token))
        assert resp.status_code == 403, resp.text

    def test_admin_sees_empty_initially(self, client):
        resp = client.get("/audit/logs")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_action_filter_and_pagination(self, client, test_db, admin_user_id):
        _seed_log(test_db, admin_user_id, "pipeline.run")
        _seed_log(test_db, admin_user_id, "pipeline.run")
        _seed_log(test_db, admin_user_id, "file.upload")

        resp = client.get("/audit/logs", params={"action": "file.upload"})
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["action"] == "file.upload"

        page1 = client.get("/audit/logs", params={"limit": 2, "page": 1})
        page2 = client.get("/audit/logs", params={"limit": 2, "page": 2})
        assert page1.status_code == 200 and page2.status_code == 200
        assert len(page1.json()) == 2
        assert len(page2.json()) == 1
        ids1 = {row["id"] for row in page1.json()}
        ids2 = {row["id"] for row in page2.json()}
        assert ids1.isdisjoint(ids2)

    def test_invalid_pagination_rejected(self, client):
        assert client.get("/audit/logs", params={"page": 0}).status_code == 422
        assert client.get("/audit/logs", params={"limit": 101}).status_code == 422

    def test_row_shape(self, client, test_db, admin_user_id):
        _seed_log(test_db, admin_user_id, "pipeline.run")
        (row,) = client.get("/audit/logs").json()
        assert set(row) >= {
            "id", "user_id", "action", "resource_type",
            "resource_id", "created_at",
        }
        assert row["user_id"] == admin_user_id
        assert row["action"] == "pipeline.run"


class TestAuditLogsMine:
    def test_requires_auth(self, auth_client):
        assert auth_client.get("/audit/logs/mine").status_code == 401

    def test_scoped_to_current_user(self, client, test_db, viewer_token, admin_user_id):
        token, viewer_id = viewer_token
        _seed_log(test_db, admin_user_id, "pipeline.run")
        _seed_log(test_db, viewer_id, "file.upload")

        mine = client.get("/audit/logs/mine", headers=_auth_header(token))
        assert mine.status_code == 200, mine.text
        rows = mine.json()
        assert len(rows) == 1
        assert rows[0]["user_id"] == viewer_id
        assert rows[0]["action"] == "file.upload"


class TestDashboardStats:
    def test_requires_auth(self, auth_client):
        assert auth_client.get("/api/v1/dashboard/stats").status_code == 401

    def test_shape_for_fresh_admin(self, client):
        resp = client.get("/api/v1/dashboard/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == 0
        assert body["total_files"] == 0
        for key in (
            "completed", "failed", "pending", "running", "healing",
            "success_rate", "recent_activity",
        ):
            assert key in body, f"missing dashboard key: {key}"
