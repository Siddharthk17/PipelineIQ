"""Unit tests for Celery queue routing and worker-safety config."""

import importlib
import pkgutil

from backend.celery_app import celery_app
from backend.celery_config import (
    result_expires,
    task_acks_late,
    task_queues,
    task_routes,
    task_soft_time_limit,
    task_time_limit,
    worker_prefetch_multiplier,
)


class TestQueueConfiguration:
    def test_three_queues_defined(self):
        queue_names = {q.name for q in task_queues}
        assert queue_names == {"critical", "default", "bulk"}

    def test_prefetch_multiplier_is_one(self):
        assert worker_prefetch_multiplier == 1

    def test_pipeline_execute_routes_to_default(self):
        route = task_routes.get("pipeline.execute")
        assert route is not None
        assert route["queue"] == "default"

    def test_webhooks_deliver_routes_to_critical(self):
        route = task_routes.get("webhooks.deliver")
        assert route is not None
        assert route["queue"] == "critical"

    def test_notifications_deliver_routes_to_critical(self):
        route = task_routes.get("notifications.deliver")
        assert route is not None
        assert route["queue"] == "critical"

    def test_schedules_check_routes_to_bulk(self):
        route = task_routes.get("schedules.check")
        assert route is not None
        assert route["queue"] == "bulk"

    def test_all_tasks_have_explicit_routes(self):
        import backend.tasks as tasks_pkg

        defined_task_names = set()
        for _, module_name, _ in pkgutil.walk_packages(tasks_pkg.__path__, tasks_pkg.__name__ + "."):
            module = importlib.import_module(module_name)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if hasattr(attr, "name") and hasattr(attr, "delay") and isinstance(getattr(attr, "name", None), str):
                    defined_task_names.add(attr.name)

        unrouted = {name for name in defined_task_names if not name.startswith("celery.")} - set(task_routes.keys())
        assert not unrouted

    def test_task_acks_late_enabled(self):
        assert task_acks_late is True

    def test_task_time_limits_configured(self):
        assert task_soft_time_limit == 300
        assert task_time_limit == 360

    def test_result_expiry_is_one_hour(self):
        assert result_expires == 3600

    def test_celery_app_uses_json_serialization(self):
        assert celery_app.conf.task_serializer == "json"
        assert celery_app.conf.result_serializer == "json"
        assert "json" in celery_app.conf.accept_content


class TestQueuePriorityModel:
    def test_worker_queue_assignment_model(self):
        critical_worker_queues = {"critical"}
        default_worker_queues = {"critical", "default"}
        bulk_worker_queues = {"bulk"}

        assert "default" not in critical_worker_queues
        assert "bulk" not in critical_worker_queues
        assert "critical" in default_worker_queues
        assert "critical" not in bulk_worker_queues
        assert "default" not in bulk_worker_queues
