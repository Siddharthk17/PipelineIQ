"""
Beat Manager — dynamic registration of pipeline schedules with Celery Beat.

The beat_schedule is rebuilt from the database on every call so Celery Beat
can pick up changes without a restart (it reads the schedule dict on each tick).
"""
from datetime import datetime, timezone
from typing import Optional

from celery.schedules import crontab

from backend.celery_app import celery_app
from backend.database import SessionLocal
from backend.models import PipelineSchedule

from backend.scheduling.cron_utils import parse_celery_crontab


def _build_beat_schedule() -> dict:
    """
    Read all active schedules from the database and return a Celery Beat
    schedule dict:  { task_name: { 'task': '...', 'schedule': crontab(...), 'kwargs': {...} } }
    """
    db = SessionLocal()
    try:
        schedules = db.query(PipelineSchedule).filter(PipelineSchedule.is_active == True).all()
    finally:
        db.close()

    beat_schedule = {}
    for schedule in schedules:
        try:
            celery_schedule = parse_celery_crontab(schedule.cron_expression)
        except Exception:
            continue  # skip invalid cron expressions

        task_name = f"scheduled:{schedule.id}"
        beat_schedule[task_name] = {
            "task": "tasks.execute_scheduled_pipeline",
            "schedule": celery_schedule,
            "kwargs": {"schedule_id": str(schedule.id)},
        }

    return beat_schedule


# ── Public API ───────────────────────────────────────────────────────────────

def register_schedules() -> dict:
    """
    Called once at worker startup (or by an API endpoint to force a reload).
    Replaces celery_app.conf.beat_schedule with the current database schedules.
    """
    new_schedule = _build_beat_schedule()
    celery_app.conf.beat_schedule = new_schedule
    return new_schedule


def get_next_run_for_schedule(schedule_id: str) -> Optional[datetime]:
    """
    Returns the next UTC run time for a given schedule_id.
    """
    from backend.scheduling.cron_utils import get_next_run_at

    db = SessionLocal()
    try:
        schedule = db.query(PipelineSchedule).filter(PipelineSchedule.id == schedule_id).first()
        if not schedule:
            return None
        return get_next_run_at(schedule.cron_expression)
    finally:
        db.close()


def update_schedule_next_run(schedule_id: str) -> None:
    """
    Recompute and persist the next_run_at for a schedule.
    Called after a run is submitted so the next_run_at is always accurate.
    """
    from backend.scheduling.cron_utils import get_next_run_at
    from sqlalchemy import update
    from backend.database import SessionLocal
    from backend.models import PipelineSchedule

    db = SessionLocal()
    try:
        next_run = get_next_run_at(
            db.query(PipelineSchedule)
            .filter(PipelineSchedule.id == schedule_id)
            .first()
            .cron_expression
        )
        db.execute(
            update(PipelineSchedule)
            .where(PipelineSchedule.id == schedule_id)
            .values(next_run_at=next_run)
        )
        db.commit()
    finally:
        db.close()


def sync_beats_from_db() -> dict:
    """
    Same as register_schedules but also updates each schedule's next_run_at
    in the database. Call this once when the Beat worker starts.
    """
    db = SessionLocal()
    try:
        schedules = db.query(PipelineSchedule).filter(PipelineSchedule.is_active == True).all()
    finally:
        db.close()

    for schedule in schedules:
        update_schedule_next_run(str(schedule.id))

    return register_schedules()