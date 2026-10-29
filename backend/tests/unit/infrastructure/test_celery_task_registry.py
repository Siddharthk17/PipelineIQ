"""Every routed/scheduled Celery task name must resolve (S03).

A route or beat entry pointing at an unregistered task name fails at
runtime with 'Received unregistered task' — after the triggering event
already happened. This test boots the real Celery app (which
autodiscovers all task modules) and asserts every configured name
resolves.
"""

# Import every task module so @task decorators register with the app,
# mirroring what the workers load at startup.
from backend.tasks import gemini_tasks as gemini_tasks
from backend.tasks import notification_tasks as notification_tasks
from backend.tasks import pipeline_tasks as pipeline_tasks
from backend.tasks import profiling as profiling
from backend.tasks import schedule_tasks as schedule_tasks
from backend.tasks import scheduled_pipeline as scheduled_pipeline
from backend.tasks import storage_maintenance as storage_maintenance
from backend.tasks import streaming_pipeline as streaming_pipeline
from backend.tasks import webhook_tasks as webhook_tasks

_REGISTERED_TASK_MODULES = (
    gemini_tasks,
    notification_tasks,
    pipeline_tasks,
    profiling,
    schedule_tasks,
    scheduled_pipeline,
    storage_maintenance,
    streaming_pipeline,
    webhook_tasks,
)
from backend.celery_app import celery_app
from backend.celery_config import beat_schedule, task_routes


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
