"""Catalog reads are scoped to the asset owner (S20g).

Search, blast radius, lineage, orphans and stats must never surface
another user's assets. Admins keep the global view.
"""

import uuid
from datetime import datetime, timezone

from backend.models import AssetRelationship, DataAsset, User


def _register_login(auth_client, email, username):
    auth_client.post(
        "/auth/register",
        json={"email": email, "username": username,
              "password": "Testpass123!"},
    )
    resp = auth_client.post(
        "/auth/login", json={"email": email, "password": "Testpass123!"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _seed(auth_client, test_db, tag):
    """Two users, each owning a file asset; B also owns a child column."""
    _register_login(auth_client, f"ca_{tag}@t.local", f"ca_{tag}")
    _register_login(auth_client, f"cb_{tag}@t.local", f"cb_{tag}")
    user_a = test_db.query(User).filter_by(
        email=f"ca_{tag}@t.local").one()
    user_b = test_db.query(User).filter_by(
        email=f"cb_{tag}@t.local").one()
    # Pin non-admin roles: the shared test DB may already contain users,
    # which would otherwise make the first registration an admin.
    user_a.role = "viewer"
    user_b.role = "viewer"
    test_db.commit()
    now = datetime.now(timezone.utc)
    a_file = DataAsset(
        asset_type="file", name=f"a_secret_{tag}.csv", namespace="files",
        owner_id=user_a.id, last_seen_at=now)
    b_file = DataAsset(
        asset_type="file", name=f"b_secret_{tag}.csv", namespace="files",
        owner_id=user_b.id, last_seen_at=now)
    b_col = DataAsset(
        asset_type="column", name=f"b_secret_col_{tag}", namespace="files",
        owner_id=user_b.id, last_seen_at=now)
    test_db.add_all([a_file, b_file, b_col])
    test_db.flush()
    test_db.add(
        AssetRelationship(
            source_id=b_file.id, target_id=b_col.id,
            relation="transforms", pipeline_name=f"pipe_{tag}"))
    test_db.commit()
    login_b = auth_client.post(
        "/auth/login",
        json={"email": f"cb_{tag}@t.local", "password": "Testpass123!"})
    headers_b = {
        "Authorization": f"Bearer {login_b.json()['access_token']}"}
    login_a = auth_client.post(
        "/auth/login",
        json={"email": f"ca_{tag}@t.local", "password": "Testpass123!"})
    headers_a = {
        "Authorization": f"Bearer {login_a.json()['access_token']}"}
    return headers_a, headers_b, tag


def test_search_hides_foreign_assets(auth_client, test_db):
    tag = uuid.uuid4().hex[:8]
    headers_a, headers_b, tag = _seed(auth_client, test_db, tag)

    resp = auth_client.get(f"/api/catalog/search?q=b_secret_{tag}",
                           headers=headers_a)
    assert resp.status_code == 200, resp.text
    names = [r["name"] for r in resp.json()["results"]]
    assert not any(n.startswith("b_secret_") for n in names), names

    resp = auth_client.get(f"/api/catalog/search?q=b_secret_{tag}",
                           headers=headers_b)
    assert resp.status_code == 200, resp.text
    names = [r["name"] for r in resp.json()["results"]]
    assert f"b_secret_{tag}.csv" in names, names


def test_impact_hides_foreign_assets(auth_client, test_db):
    tag = uuid.uuid4().hex[:8]
    headers_a, headers_b, tag = _seed(auth_client, test_db, tag)

    resp = auth_client.get(
        f"/api/catalog/assets/b_secret_{tag}.csv/impact",
        headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["downstream"] == [], resp.text

    resp = auth_client.get(
        f"/api/catalog/assets/b_secret_{tag}.csv/impact",
        headers=headers_b)
    assert resp.status_code == 200, resp.text
    downstream = [d["name"] for d in resp.json()["downstream"]]
    assert f"b_secret_col_{tag}" in downstream, downstream


def test_lineage_hides_foreign_assets(auth_client, test_db):
    tag = uuid.uuid4().hex[:8]
    headers_a, headers_b, tag = _seed(auth_client, test_db, tag)

    resp = auth_client.get(
        f"/api/catalog/assets/b_secret_col_{tag}/lineage",
        headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["upstream"] == [], resp.text

    resp = auth_client.get(
        f"/api/catalog/assets/b_secret_col_{tag}/lineage",
        headers=headers_b)
    assert resp.status_code == 200, resp.text
    upstream = [u["name"] for u in resp.json()["upstream"]]
    assert f"b_secret_{tag}.csv" in upstream, upstream


def test_stats_count_only_own_assets(auth_client, test_db):
    tag = uuid.uuid4().hex[:8]
    headers_a, headers_b, tag = _seed(auth_client, test_db, tag)

    resp = auth_client.get("/api/catalog/stats", headers=headers_a)
    assert resp.status_code == 200, resp.text
    assert resp.json()["assets_by_type"] == {"file": 1}, resp.text

    resp = auth_client.get("/api/catalog/stats", headers=headers_b)
    assert resp.status_code == 200, resp.text
    assert resp.json()["assets_by_type"] == {"file": 1, "column": 1}, resp.text
    assert resp.json()["total_relationships"] == 1, resp.text
