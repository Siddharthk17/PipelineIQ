"""Tests for SSE streaming hardening and reconnect behavior."""

import uuid
from unittest.mock import MagicMock

import orjson
import pytest
from fastapi.testclient import TestClient

import backend.api.sse as sse_module
from backend.auth import create_access_token, get_password_hash
from backend.database import get_db, get_read_db
from backend.main import app
from backend.models import PipelineRun, PipelineStatus, User
from backend.pipeline.runner import StepProgressEvent, StepStatus
from backend.sse_app import sse_app
from backend.tasks import pipeline_tasks


@pytest.fixture()
def sse_client(test_db):
    """SSE app client bound to the in-memory test DB."""

    def override_get_db():
        yield test_db

    sse_app.dependency_overrides[get_db] = override_get_db
    sse_app.dependency_overrides[get_read_db] = override_get_db
    client = TestClient(sse_app)
    try:
        yield client
    finally:
        sse_app.dependency_overrides.clear()


@pytest.fixture()
def no_auth_main_client(test_db):
    """Main app client without auth dependency overrides for route availability checks."""

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


def _create_user(test_db, email: str, username: str, role: str = "viewer") -> User:
    user = User(
        email=email,
        username=username,
        hashed_password=get_password_hash("Str0ngP@ss!"),
        role=role,
        is_active=True,
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


def _create_run(
    test_db, owner_id, status: PipelineStatus = PipelineStatus.RUNNING
) -> PipelineRun:
    run = PipelineRun(
        name="sse-run",
        status=status,
        yaml_config="pipeline:\n  name: sse-run\n  steps: []",
        user_id=owner_id,
    )
    test_db.add(run)
    test_db.commit()
    test_db.refresh(run)
    return run


def _token_for(user: User) -> str:
    return create_access_token({"sub": str(user.id), "role": user.role})


def test_sse_stream_requires_query_token(sse_client):
    run_id = str(uuid.uuid4())
    response = sse_client.get(f"/api/v1/pipelines/{run_id}/stream")
    assert response.status_code == 401


def test_sse_stream_forbidden_for_non_owner(sse_client, test_db):
    owner = _create_user(test_db, "owner@test.com", "owner")
    other = _create_user(test_db, "other@test.com", "other")
    run = _create_run(test_db, owner.id)
    token = _token_for(other)

    response = sse_client.get(f"/api/v1/pipelines/{run.id}/stream?token={token}")
    assert response.status_code == 403


def test_sse_stream_owner_receives_events(sse_client, test_db, monkeypatch):
    owner = _create_user(test_db, "owner2@test.com", "owner2")
    run = _create_run(test_db, owner.id)
    token = _token_for(owner)

    async def fake_live_event_generator(run_id: str, request, initial_status: str):
        _ = request
        _ = initial_status
        yield f"event: progress\ndata: {orjson.dumps({'run_id': run_id}).decode('utf-8')}\n\n"

    monkeypatch.setattr(sse_module, "_live_event_generator", fake_live_event_generator)
    response = sse_client.get(f"/api/v1/pipelines/{run.id}/stream?token={token}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in response.text


def test_terminal_run_emits_stream_end_event(sse_client, test_db):
    owner = _create_user(test_db, "owner3@test.com", "owner3")
    run = _create_run(test_db, owner.id, status=PipelineStatus.COMPLETED)
    token = _token_for(owner)

    response = sse_client.get(f"/api/v1/pipelines/{run.id}/stream?token={token}")
    assert response.status_code == 200
    assert "event: pipeline_completed" in response.text
    assert "event: stream_end" in response.text


def test_stream_endpoint_not_served_by_main_app(no_auth_main_client):
    run_id = str(uuid.uuid4())
    response = no_auth_main_client.get(f"/api/v1/pipelines/{run_id}/stream")
    assert response.status_code == 404


class _FakeRequest:
    def __init__(self, states):
        self._states = list(states)
        self._index = 0

    async def is_disconnected(self) -> bool:
        if self._index >= len(self._states):
            return self._states[-1] if self._states else False
        value = self._states[self._index]
        self._index += 1
        return value


class _FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.unsubscribed = False
        self.closed = False
        self.subscribed_channel = None

    async def subscribe(self, channel: str):
        self.subscribed_channel = channel

    async def get_message(self, ignore_subscribe_messages=True, timeout=1.0):
        _ = ignore_subscribe_messages
        _ = timeout
        if self._messages:
            return self._messages.pop(0)
        return None

    async def unsubscribe(self, channel: str):
        _ = channel
        self.unsubscribed = True

    async def aclose(self):
        self.closed = True


class _FakeRedisClient:
    def __init__(self, pubsub):
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self):
        return self._pubsub

    async def aclose(self):
        self.closed = True


class _FakeCacheClient:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    async def get(self, key: str):
        _ = key
        return self.payload

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_live_generator_emits_cached_terminal_and_stream_end(monkeypatch):
    payload = orjson.dumps(
        {
            "run_id": "r-1",
            "event_type": "pipeline_completed",
            "status": "COMPLETED",
        }
    )
    fake_pubsub = _FakePubSub(messages=[])
    fake_pubsub_client = _FakeRedisClient(fake_pubsub)
    fake_cache = _FakeCacheClient(payload)

    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: fake_pubsub_client
    )
    monkeypatch.setattr(sse_module, "get_cache_redis_async", lambda: fake_cache)

    request = _FakeRequest([False])
    generator = sse_module._live_event_generator("r-1", request, "RUNNING")
    events = [item async for item in generator]

    assert len(events) == 2
    assert "event: pipeline_completed" in events[0]
    assert "event: stream_end" in events[1]
    assert fake_pubsub.unsubscribed is True
    assert fake_pubsub.closed is True
    assert fake_pubsub_client.closed is True
    assert fake_cache.closed is True


@pytest.mark.asyncio
async def test_live_generator_emits_heartbeat_when_idle(monkeypatch):
    fake_pubsub = _FakePubSub(messages=[])
    fake_pubsub_client = _FakeRedisClient(fake_pubsub)
    fake_cache = _FakeCacheClient(None)

    monotonic_values = iter([0.0, 16.0, 16.0, 16.0, 16.0])

    def _fake_monotonic():
        try:
            return next(monotonic_values)
        except StopIteration:
            return 16.0

    monkeypatch.setattr(sse_module.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: fake_pubsub_client
    )
    monkeypatch.setattr(sse_module, "get_cache_redis_async", lambda: fake_cache)

    request = _FakeRequest([False, True])
    generator = sse_module._live_event_generator("r-2", request, "RUNNING")
    first = await anext(generator)
    assert first.startswith("event: pipeline_status\ndata: ")
    assert '"run_id":"r-2"' in first
    assert '"status":"RUNNING"' in first
    second = await anext(generator)
    assert second == ": heartbeat\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(generator)

    assert fake_pubsub.unsubscribed is True
    assert fake_pubsub.closed is True
    assert fake_pubsub_client.closed is True
    assert fake_cache.closed is True


def test_progress_callback_publishes_and_caches_status(monkeypatch):
    fake_pubsub = MagicMock()
    fake_cache = MagicMock()
    monkeypatch.setattr(pipeline_tasks, "get_pubsub_redis", lambda: fake_pubsub)
    monkeypatch.setattr(pipeline_tasks, "get_cache_redis", lambda: fake_cache)

    callback = pipeline_tasks.make_redis_progress_callback("run-cache")
    callback(
        StepProgressEvent(
            run_id="run-cache",
            step_name="load_sales",
            step_index=0,
            total_steps=3,
            status=StepStatus.RUNNING,
        )
    )

    fake_pubsub.publish.assert_called_once()
    fake_cache.setex.assert_called_once()
    key_arg = fake_cache.setex.call_args.args[0]
    assert key_arg == "pipeline_progress:last:run-cache"


def test_terminal_publish_caches_latest_terminal_status(monkeypatch):
    fake_pubsub = MagicMock()
    fake_cache = MagicMock()
    monkeypatch.setattr(pipeline_tasks, "get_pubsub_redis", lambda: fake_pubsub)
    monkeypatch.setattr(pipeline_tasks, "get_cache_redis", lambda: fake_cache)

    pipeline_tasks._publish_terminal_event("run-term", "pipeline_completed")

    fake_pubsub.publish.assert_called_once()
    fake_cache.setex.assert_called_once()
    key_arg = fake_cache.setex.call_args.args[0]
    payload_arg = fake_cache.setex.call_args.args[2]
    parsed = orjson.loads(payload_arg)
    assert key_arg == "pipeline_progress:last:run-term"
    assert parsed["status"] == "COMPLETED"
