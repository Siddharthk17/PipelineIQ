"""Every routed/scheduled Celery task name must resolve (S03).

A route or beat entry pointing at an unregistered task name fails at
runtime with 'Received unregistered task' — after the triggering event
already happened. This test boots the real Celery app (which
autodiscovers all task modules) and asserts every configured name
resolves.
"""

from backend.celery_app import celery_app
from backend.celery_config import beat_schedule, task_routes

# Import every task module so @task decorators register with the app,
# mirroring what the workers load at startup.
import backend.tasks.gemini_tasks  # noqa: F401
import backend.tasks.notification_tasks  # noqa: F401
import backend.tasks.pipeline_tasks  # noqa: F401
import backend.tasks.profiling  # noqa: F401
import backend.tasks.scheduled_pipeline  # noqa: F401
import backend.tasks.schedule_tasks  # noqa: F401
import backend.tasks.storage_maintenance  # noqa: F401
import backend.tasks.streaming_pipeline  # noqa: F401
import backend.tasks.webhook_tasks  # noqa: F401


def _registered_names() -> set[str]:
    return set(celery_app.tasks.keys())


def test_all_task_routes_resolve():
    registered = _registered_names()
    orphans = [name for name in task_routes if name not in registered]
    assert not orphans, f"task routes with no registered task: {orphans}"


def test_all_beat_tasks_resolve():
    registered = _registered_names()
    orphans = [
        entry["task"]
        for entry in beat_schedule.values()
        if entry["task"] not in registered
    ]
    assert not orphans, f"beat entries with no registered task: {orphans}"
