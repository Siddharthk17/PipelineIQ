from unittest.mock import MagicMock


def test_request_validation_errors_are_logged(client, monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr("backend.main.structlog.get_logger", lambda *args, **kwargs: mock_logger)

    response = client.post("/api/v1/pipelines/run", json={})

    assert response.status_code == 422
    mock_logger.error.assert_called_once()
    event_name = mock_logger.error.call_args.args[0]
    payload = mock_logger.error.call_args.kwargs
    assert event_name == "validation_error"
    assert payload["method"] == "POST"
    assert payload["url"].endswith("/api/v1/pipelines/run")
    assert payload["errors"]


def test_redis_outage_returns_503_not_500(client, monkeypatch):
    """Cache/queue outages surface as honest 503s (S-new).

    The rate limiter reads Redis on the request path; when Redis is down
    every limited endpoint must fail with 503 + request_id, never an
    opaque 500.
    """
    import backend.utils.rate_limiter as limiter_mod
    from redis.exceptions import ConnectionError as RedisConnectionError

    def _boom(*args, **kwargs):
        raise RedisConnectionError("Error 111 connecting to redis:6381. Connection refused.")

    monkeypatch.setattr(
        limiter_mod.limiter, "_Limiter__evaluate_limits", _boom
    )
    response = client.post(
        "/auth/login",
        json={"email": "demo@pipelineiq.app", "password": "Demo1234!"},
    )
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["error_type"] == "ServiceUnavailable"
    assert body["request_id"]
