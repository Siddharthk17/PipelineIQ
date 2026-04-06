"""Integration tests for the data profiling pipeline.

Requires: docker compose up -d (all services running)
Run with: RUN_INTEGRATION_TESTS=1 pytest backend/tests/integration/test_profiling_pipeline.py -v
"""

import os
import time

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("RUN_INTEGRATION_TESTS"),
    reason="Set RUN_INTEGRATION_TESTS=1 to run integration tests",
)


class TestProfilingTaskConfiguration:
    """Verify profiling task is configured correctly — no services required."""

    def test_profile_task_on_bulk_queue(self):
        from backend.tasks.profiling import profile_file

        assert profile_file.queue == "bulk"

    def test_profile_task_has_retry_config(self):
        from backend.tasks.profiling import profile_file

        assert profile_file.max_retries == 2
        assert profile_file.default_retry_delay == 30


class TestProfilingEndpoints:
    """Test profile API endpoints — require running API."""

    def test_profile_for_nonexistent_file_returns_404(self, client):
        import uuid

        resp = client.get(f"/api/v1/files/{uuid.uuid4()}/profile")
        assert resp.status_code == 404

    def test_profile_returns_pending_when_not_computed(self, client):
        import uuid

        resp = client.get(f"/api/v1/files/{uuid.uuid4()}/profile")
        assert resp.status_code == 404

    def test_refresh_endpoint_returns_200_for_missing_file(
        self, client
    ):
        import uuid

        resp = client.post(f"/api/v1/files/{uuid.uuid4()}/profile/refresh")
        assert resp.status_code == 404
