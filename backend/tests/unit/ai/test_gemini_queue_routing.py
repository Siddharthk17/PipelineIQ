"""Tests for Gemini queue routing and execution limits."""

from backend.celery_config import task_queues, task_routes
from backend.tasks.gemini_tasks import (
    TOKEN_BUDGET_PER_MINUTE,
    TOKEN_BUDGET_WINDOW_SECONDS,
    call_gemini_task,
)


class TestGeminiQueueConfig:
    def test_call_gemini_task_is_on_gemini_queue(self):
        assert call_gemini_task.queue == "gemini"

    def test_call_gemini_route_points_to_gemini_queue(self):
        assert task_routes["tasks.call_gemini"]["queue"] == "gemini"

    def test_gemini_queue_exists(self):
        queue_names = {queue.name for queue in task_queues}
        assert "gemini" in queue_names

    def test_retry_and_rate_limits(self):
        assert call_gemini_task.max_retries == 5
        assert call_gemini_task.rate_limit == "50/m"

    def test_task_time_limits_are_set(self):
        options = call_gemini_task._get_exec_options()
        assert options["soft_time_limit"] == 120
        assert options["time_limit"] == 150


class TestGeminiBudgetConstants:
    def test_token_budget_constants(self):
        assert TOKEN_BUDGET_PER_MINUTE == 900_000
        assert TOKEN_BUDGET_WINDOW_SECONDS == 60

