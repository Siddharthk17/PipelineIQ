from unittest.mock import MagicMock


def test_request_validation_errors_are_logged(client, monkeypatch):
    mock_logger = MagicMock()
    monkeypatch.setattr("backend.main.structlog.get_logger", lambda: mock_logger)

    response = client.post("/api/v1/pipelines/run", json={})

    assert response.status_code == 422
    mock_logger.error.assert_called_once()
    event_name = mock_logger.error.call_args.args[0]
    payload = mock_logger.error.call_args.kwargs
    assert event_name == "validation_error"
    assert payload["method"] == "POST"
    assert payload["url"].endswith("/api/v1/pipelines/run")
    assert payload["errors"]
