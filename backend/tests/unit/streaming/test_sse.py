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


def _create_user(
        test_db,
        email: str,
        username: str,
        role: str = "viewer") -> User:
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


def test_sse_stream_requires_auth(sse_client):
    # No credentials at all (no header, no cookie, no query) → 401.
    run_id = str(uuid.uuid4())
    response = sse_client.get(f"/api/v1/pipelines/{run_id}/stream")
    assert response.status_code == 401


def test_sse_stream_query_token_rejected_and_non_owner_forbidden(
    sse_client, test_db
):
    # M-068: ?token= query auth removed (URLs leak via history/proxies/
    # logs) — a valid token in the query string is ignored, so 401.
    # The owner gate itself still holds via header auth (403).
    owner = _create_user(test_db, "owner@test.com", "owner")
    other = _create_user(test_db, "other@test.com", "other")
    run = _create_run(test_db, owner.id)

    response = sse_client.get(
        f"/api/v1/pipelines/{run.id}/stream?token={_token_for(other)}")
    assert response.status_code == 401

    response = sse_client.get(
        f"/api/v1/pipelines/{run.id}/stream",
        headers={"Authorization": f"Bearer {_token_for(other)}"})
    assert response.status_code == 403


def test_sse_stream_owner_receives_events(sse_client, test_db, monkeypatch):
    owner = _create_user(test_db, "owner2@test.com", "owner2")
    run = _create_run(test_db, owner.id)
    token = _token_for(owner)

    async def fake_live_event_generator(
            run_id: str, request, initial_status: str, user_key: str = ""):
        _ = request
        _ = initial_status
        _ = user_key
        yield f"event: progress\ndata: {orjson.dumps({'run_id': run_id}).decode('utf-8')}\n\n"
        if user_key:
            await sse_module._release_stream_slot(user_key)

    async def _no_preflight(run_id: str) -> None:
        _ = run_id

    monkeypatch.setattr(
        sse_module,
        "_live_event_generator",
        fake_live_event_generator)
    monkeypatch.setattr(
        sse_module, "_require_redis_available", _no_preflight)
    # M-068: auth via Authorization header (cookies also work); ?token=
    # query auth was removed.
    response = sse_client.get(
        f"/api/v1/pipelines/{run.id}/stream",
        headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in response.text


def test_terminal_run_emits_stream_end_event(sse_client, test_db):
    owner = _create_user(test_db, "owner3@test.com", "owner3")
    run = _create_run(test_db, owner.id, status=PipelineStatus.COMPLETED)
    token = _token_for(owner)

    # M-068: auth via Authorization header; ?token= query auth removed.
    response = sse_client.get(
        f"/api/v1/pipelines/{run.id}/stream",
        headers={"Authorization": f"Bearer {token}"})
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

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


class _FakeCacheClient:
    def __init__(self, payload):
        self.payload = payload
        self.closed = False

    async def get(self, key: str):
        _ = key
        return self.payload

    async def ping(self):
        return True

    async def aclose(self):
        self.closed = True


@pytest.fixture()
def _clean_sse_state():
    """Reset SSE caps + circuit between tests (isolation)."""
    sse_module._ACTIVE_STREAMS.clear()
    sse_module._ACTIVE_STREAMS_TOTAL = 0
    sse_module._SSE_CIRCUIT["failures"] = 0
    sse_module._SSE_CIRCUIT["open_until"] = 0.0
    yield
    sse_module._ACTIVE_STREAMS.clear()
    sse_module._ACTIVE_STREAMS_TOTAL = 0
    sse_module._SSE_CIRCUIT["failures"] = 0
    sse_module._SSE_CIRCUIT["open_until"] = 0.0


@pytest.mark.asyncio
async def test_live_generator_emits_cached_terminal_and_stream_end(
        monkeypatch, _clean_sse_state):
    from backend.utils.sse_security import sign_sse_payload
    payload = orjson.dumps(
        sign_sse_payload({
            "run_id": "r-1",
            "event_type": "pipeline_completed",
            "status": "COMPLETED",
        })
    )
    fake_pubsub = _FakePubSub(messages=[])
    fake_pubsub_client = _FakeRedisClient(fake_pubsub)
    fake_cache = _FakeCacheClient(payload)

    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: fake_pubsub_client
    )
    monkeypatch.setattr(
        sse_module,
        "get_cache_redis_async",
        lambda: fake_cache)

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
async def test_live_generator_emits_heartbeat_when_idle(
        monkeypatch, _clean_sse_state):
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
    monkeypatch.setattr(
        sse_module,
        "get_cache_redis_async",
        lambda: fake_cache)

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
    monkeypatch.setattr(
        pipeline_tasks,
        "get_pubsub_redis",
        lambda: fake_pubsub)
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
    monkeypatch.setattr(
        pipeline_tasks,
        "get_pubsub_redis",
        lambda: fake_pubsub)
    monkeypatch.setattr(pipeline_tasks, "get_cache_redis", lambda: fake_cache)

    pipeline_tasks._publish_terminal_event(
        "run-term", "pipeline_completed", "COMPLETED")

    fake_pubsub.publish.assert_called_once()
    fake_cache.setex.assert_called_once()
    key_arg = fake_cache.setex.call_args.args[0]
    payload_arg = fake_cache.setex.call_args.args[2]
    parsed = orjson.loads(payload_arg)
    assert key_arg == "pipeline_progress:last:run-term"
    assert parsed["status"] == "COMPLETED"


# --- Wave 2 SSE remediation (M-156, M-106, M-170, M-171) ---


def test_unsigned_payload_rejected_by_default(_clean_sse_state):
    """M-156: no fail-open in test env — unsigned payloads rejected."""
    from backend.utils.sse_security import verify_sse_payload
    assert verify_sse_payload({"run_id": "x"}) is False


def test_unsigned_opt_in_flag(monkeypatch, _clean_sse_state):
    """M-156: explicit opt-in allows unsigned, never in production."""
    from backend.config import settings
    from backend.utils import sse_security
    monkeypatch.setenv("PIPELINEIQ_ALLOW_UNSIGNED_SSE", "true")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    assert sse_security.verify_sse_payload({"run_id": "x"}) is True
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert sse_security.verify_sse_payload({"run_id": "x"}) is False
    monkeypatch.setenv("PIPELINEIQ_ALLOW_UNSIGNED_SSE", "false")
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    assert sse_security.verify_sse_payload({"run_id": "x"}) is False


@pytest.mark.asyncio
async def test_unsigned_cache_payload_ignored(
        monkeypatch, _clean_sse_state):
    """M-156: unsigned cached payloads are not served to streams."""
    unsigned = orjson.dumps({"run_id": "r-u", "status": "COMPLETED"})
    fake_cache = _FakeCacheClient(unsigned)
    monkeypatch.setattr(
        sse_module, "get_cache_redis_async", lambda: fake_cache)
    assert await sse_module._get_cached_event(fake_cache, "r-u") is None


@pytest.mark.asyncio
async def test_per_user_stream_cap_enforced(_clean_sse_state):
    """M-106: the (cap+1)-th concurrent stream for a user gets 429."""
    from fastapi import HTTPException
    for _ in range(sse_module.SSE_MAX_STREAMS_PER_USER):
        await sse_module._acquire_stream_slot("user-cap")
    with pytest.raises(HTTPException) as exc_info:
        await sse_module._acquire_stream_slot("user-cap")
    assert exc_info.value.status_code == 429
    await sse_module._release_stream_slot("user-cap")
    # Slot freed — acquire succeeds again.
    await sse_module._acquire_stream_slot("user-cap")
    await sse_module._release_stream_slot("user-cap")


@pytest.mark.asyncio
async def test_backpressure_coalesces_bursts(
        monkeypatch, _clean_sse_state):
    """M-106: bursts of progress events collapse to latest; terminal kept."""
    from backend.utils.sse_security import sign_sse_payload

    def _msg(status, event_type="progress"):
        return {"data": orjson.dumps(
            sign_sse_payload(
                {"run_id": "r-b", "event_type": event_type,
                 "status": status}))}

    burst = [_msg("RUNNING") for _ in range(5)] + [
        _msg("COMPLETED", "pipeline_completed")]
    fake_pubsub = _FakePubSub(messages=list(burst))
    fake_pubsub_client = _FakeRedisClient(fake_pubsub)
    fake_cache = _FakeCacheClient(None)
    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: fake_pubsub_client)
    monkeypatch.setattr(
        sse_module, "get_cache_redis_async", lambda: fake_cache)

    request = _FakeRequest([False] * 30)
    events = [
        item async for item in
        sse_module._live_event_generator("r-b", request, "RUNNING")]
    # Initial status + coalesced progress + terminal + stream_end.
    assert any("pipeline_status" in e for e in events)
    assert any("pipeline_completed" in e for e in events)
    assert events[-1].startswith("event: stream_end")
    assert len(events) <= 5


@pytest.mark.asyncio
async def test_redis_outage_closes_stream_bounded(
        monkeypatch, _clean_sse_state):
    """M-171: sustained Redis failures end the stream (no retry storm)."""
    import redis.exceptions as redis_exc

    class _BoomPubSub(_FakePubSub):
        async def get_message(self, ignore_subscribe_messages=True,
                              timeout=1.0):
            raise redis_exc.ConnectionError("down")

    async def _boom_subscribe(channel: str):
        return None

    boom = _BoomPubSub(messages=[])
    boom.subscribe = _boom_subscribe  # type: ignore[method-assign]
    fake_client = _FakeRedisClient(boom)
    fake_cache = _FakeCacheClient(None)
    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: fake_client)
    monkeypatch.setattr(
        sse_module, "get_cache_redis_async", lambda: fake_cache)

    request = _FakeRequest([False] * 30)
    events = [
        item async for item in
        sse_module._live_event_generator("r-c", request, "RUNNING")]
    # Only the initial status event, then a bounded degraded close.
    assert len(events) == 1
    assert boom.unsubscribed is True


@pytest.mark.asyncio
async def test_require_redis_down_returns_503(
        monkeypatch, _clean_sse_state):
    """M-170: pre-flight failure raises honest 503, not a dead 200."""
    import redis.exceptions as redis_exc
    from fastapi import HTTPException

    class _DownClient:
        async def ping(self):
            raise redis_exc.ConnectionError("cache down")

        async def aclose(self):
            return None

    monkeypatch.setattr(
        sse_module, "get_pubsub_redis_async", lambda: _DownClient())
    monkeypatch.setattr(
        sse_module, "get_cache_redis_async", lambda: _DownClient())
    with pytest.raises(HTTPException) as exc_info:
        await sse_module._require_redis_available("r-503")
    assert exc_info.value.status_code == 503
