"""Tests for JWT authentication endpoints."""

import time
from unittest.mock import patch

import pytest
from jose import jwt

from backend.auth import ALGORITHM, get_password_hash, create_access_token


# ── Helpers ─────────────────────────────────────────────────────────

def register_user(auth_client, email="test@example.com", username="testuser", password="Str0ngP@ss!"):
    return auth_client.post("/auth/register", json={
        "email": email,
        "username": username,
        "password": password,
    })


def login_user(auth_client, email="test@example.com", password="Str0ngP@ss!"):
    return auth_client.post("/auth/login", json={
        "email": email,
        "password": password,
    })


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ── Registration ────────────────────────────────────────────────────

def test_register_first_user_becomes_admin(auth_client):
    r = register_user(auth_client, "admin1@test.com", "admin1")
    assert r.status_code == 201
    assert r.json()["role"] == "admin"


def test_register_second_user_becomes_viewer(auth_client):
    register_user(auth_client, "first@test.com", "firstuser")
    r = register_user(auth_client, "second@test.com", "seconduser")
    assert r.status_code == 201
    assert r.json()["role"] == "viewer"


def test_register_duplicate_email_returns_409(auth_client):
    register_user(auth_client, "dup@test.com", "user1")
    r = register_user(auth_client, "dup@test.com", "user2")
    assert r.status_code == 409


def test_register_duplicate_username_returns_409(auth_client):
    register_user(auth_client, "a@test.com", "dupname")
    r = register_user(auth_client, "b@test.com", "dupname")
    assert r.status_code == 409


def test_register_invalid_email_returns_422(auth_client):
    r = register_user(auth_client, "not-an-email", "validuser")
    assert r.status_code == 422


def test_register_short_password_returns_422(auth_client):
    r = register_user(auth_client, "valid@test.com", "validuser", "short")
    assert r.status_code == 422


# ── Login ───────────────────────────────────────────────────────────

def test_login_valid_credentials_returns_token(auth_client):
    register_user(auth_client, "login@test.com", "loginuser")
    r = login_user(auth_client, "login@test.com")
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] > 0
    assert data["user"]["email"] == "login@test.com"


def test_login_wrong_password_returns_401(auth_client):
    register_user(auth_client, "wrong@test.com", "wronguser")
    r = login_user(auth_client, "wrong@test.com", "WrongPassword!")
    assert r.status_code == 401


def test_login_nonexistent_user_returns_401(auth_client):
    r = login_user(auth_client, "noone@test.com", "whatever123")
    assert r.status_code == 401


# ── Token / Profile ────────────────────────────────────────────────

def test_get_me_with_valid_token(auth_client):
    register_user(auth_client, "me@test.com", "meuser")
    token = login_user(auth_client, "me@test.com").json()["access_token"]
    r = auth_client.get("/auth/me", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["email"] == "me@test.com"


def test_get_me_with_invalid_token_returns_401(auth_client):
    r = auth_client.get("/auth/me", headers=auth_header("invalid.token.here"))
    assert r.status_code == 401


def test_get_me_with_expired_token_returns_401(auth_client):
    register_user(auth_client, "exp@test.com", "expuser")
    # Create a token that expired 1 second ago
    from datetime import datetime, timedelta
    from backend.config import settings
    payload = {"sub": "fake-id", "exp": datetime.utcnow() - timedelta(seconds=1)}
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)
    r = auth_client.get("/auth/me", headers=auth_header(token))
    assert r.status_code == 401


# ── Route protection ───────────────────────────────────────────────

def test_protected_route_without_token_returns_401(auth_client):
    r = auth_client.post("/api/v1/pipelines/validate", json={"yaml_config": "test"})
    assert r.status_code in [401, 403]


def test_protected_route_with_valid_token_succeeds(auth_client):
    register_user(auth_client, "auth@test.com", "authuser")
    token = login_user(auth_client, "auth@test.com").json()["access_token"]
    # Validate will return 422 for invalid yaml, but NOT 401
    r = auth_client.post(
        "/api/v1/pipelines/validate",
        json={"yaml_config": "pipeline:\n  name: test\n  steps: []"},
        headers=auth_header(token),
    )
    assert r.status_code != 401


# ── RBAC ────────────────────────────────────────────────────────────

def test_admin_route_with_viewer_token_returns_403(auth_client):
    register_user(auth_client, "adm@test.com", "admuser")  # admin (first)
    register_user(auth_client, "view@test.com", "viewuser")  # viewer
    viewer_token = login_user(auth_client, "view@test.com").json()["access_token"]
    r = auth_client.get("/auth/users", headers=auth_header(viewer_token))
    assert r.status_code == 403


def test_admin_route_with_admin_token_succeeds(auth_client):
    register_user(auth_client, "boss@test.com", "bossuser")
    admin_token = login_user(auth_client, "boss@test.com").json()["access_token"]
    r = auth_client.get("/auth/users", headers=auth_header(admin_token))
    assert r.status_code == 200


# ── Logout ──────────────────────────────────────────────────────────

def test_logout_returns_200(auth_client):
    register_user(auth_client, "bye@test.com", "byeuser")
    token = login_user(auth_client, "bye@test.com").json()["access_token"]
    r = auth_client.post("/auth/logout", headers=auth_header(token))
    assert r.status_code == 200
    assert r.json()["message"] == "Logged out successfully"
