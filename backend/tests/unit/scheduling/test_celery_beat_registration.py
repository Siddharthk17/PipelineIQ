"""Beat schedule: the 60s DB poller is the single firing mechanism (S04).

Dynamic per-schedule Beat entries were cross-process dead code (the API
process cannot rewrite the Beat scheduler's in-memory schedule), and the
DB poller in schedule_tasks is what actually fires due runs. This test
locks the static schedule so a second firing mechanism cannot be added
by accident.
"""

from backend.celery_config import beat_schedule


def test_static_beat_schedule_has_poller_and_maintenance():
    assert beat_schedule["check-pipeline-schedules"]["task"] == "schedules.check"
    assert beat_schedule["check-pipeline-schedules"]["schedule"] == 60.0
    assert beat_schedule["maintain-storage"]["task"] == "tasks.maintain_storage"
    assert beat_schedule["maintain-storage"]["schedule"] == 300.0


def test_no_dynamic_beat_entries():
    dynamic = [
        name for name in beat_schedule if name.startswith("scheduled:")
    ]
    assert dynamic == [], f"dynamic beat entries must not exist: {dynamic}"
