"""Unit tests for backend.metrics gauge write paths (M-176, M-127).

ACTIVE_USERS_TOTAL and CELERY_QUEUE_DEPTH are gauges referenced by Grafana
dashboards and the CeleryQueueDepthHigh alert, but nothing in the codebase
set them — the alerts/panels were dead. These tests pin the setter helpers
in backend.metrics, including thread-safety of concurrent writes (the
intended beat-collector pattern calls setters from scheduler threads).
"""

from __future__ import annotations

import threading

from prometheus_client import REGISTRY

from backend import metrics


def _gauge_value(name: str) -> float:
    for family in REGISTRY.collect():
        for sample in family.samples:
            if sample.name == name:
                return sample.value
    raise AssertionError(f"metric {name} not found in registry")


class TestGaugeSetters:
    def test_set_active_users_total(self):
        metrics.set_active_users_total(42)
        assert _gauge_value("pipelineiq_active_users_total") == 42

    def test_set_celery_queue_depth(self):
        metrics.set_celery_queue_depth(7)
        assert _gauge_value("pipelineiq_celery_queue_depth") == 7

    def test_setters_overwrite_previous_value(self):
        metrics.set_celery_queue_depth(3)
        metrics.set_celery_queue_depth(9)
        assert _gauge_value("pipelineiq_celery_queue_depth") == 9

    def test_concurrent_writes_do_not_raise(self):
        """Concurrent setter calls (beat-collector pattern) are safe."""
        errors: list[BaseException] = []

        def _write(value: int) -> None:
            try:
                for _ in range(50):
                    metrics.set_celery_queue_depth(value)
                    metrics.set_active_users_total(value)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_write, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert not errors
        assert _gauge_value("pipelineiq_celery_queue_depth") in range(8)
        assert _gauge_value("pipelineiq_active_users_total") in range(8)
