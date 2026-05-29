"""Tests for the schedule CRUD API endpoints."""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from backend.main import app
from backend.auth import get_current_user


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    user.email = "test@test.com"
    user.role = "admin"
    return user


class TestCronValidation:
    def test_rejects_invalid_cron_via_api(self, mock_user):
        app.dependency_overrides[get_current_user] = lambda: mock_user
        client = TestClient(app)
        try:
            response = client.post(
                "/api/v1/schedules/",
                json={
                    "pipeline_name": "test",
                    "yaml_config": "pipeline:\n  name: test\n  steps: []",
                    "cron_expression": "not-a-cron",
                },
            )
            assert response.status_code in (400, 422), f"Got {response.status_code}: {response.text}"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_rejects_empty_cron(self, mock_user):
        app.dependency_overrides[get_current_user] = lambda: mock_user
        client = TestClient(app)
        try:
            response = client.post(
                "/api/v1/schedules/",
                json={
                    "pipeline_name": "test",
                    "yaml_config": "pipeline:\n  name: test\n  steps: []",
                    "cron_expression": "",
                },
            )
            assert response.status_code in (400, 422), f"Got {response.status_code}: {response.text}"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_converts_natural_language_cron(self):
        from backend.api.schedules import CreateScheduleRequest
        req = CreateScheduleRequest(
            pipeline_name="test",
            yaml_config="pipeline:\n  name: test\n  steps: []",
            cron_expression="every hour",
        )
        assert req.cron_expression == "0 * * * *"

    def test_accepts_standard_cron(self):
        from backend.api.schedules import CreateScheduleRequest
        req = CreateScheduleRequest(
            pipeline_name="test",
            yaml_config="pipeline:\n  name: test\n  steps: []",
            cron_expression="0 6 * * 1",
        )
        assert req.cron_expression == "0 6 * * 1"

    def test_rejects_invalid_cron_pydantic(self):
        from backend.api.schedules import CreateScheduleRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CreateScheduleRequest(
                pipeline_name="test",
                yaml_config="pipeline:\n  name: test\n  steps: []",
                cron_expression="not-a-cron",
            )
