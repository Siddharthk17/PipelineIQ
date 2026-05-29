"""Tests for Celery Beat dynamic schedule registration."""
import pytest
from unittest.mock import patch, MagicMock

from backend.scheduling.beat_manager import (
    _build_beat_schedule,
    register_schedules,
    get_next_run_for_schedule,
    update_schedule_next_run,
    sync_beats_from_db,
)


class TestBuildBeatSchedule:
    def test_empty_when_no_active_schedules(self):
        with patch("backend.scheduling.beat_manager.SessionLocal") as mock_session:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = []
            mock_session.return_value = mock_db

            result = _build_beat_schedule()
            assert result == {}

    def test_maps_schedule_to_beat_entry(self):
        schedule = MagicMock()
        schedule.id = "test-uuid"
        schedule.cron_expression = "0 6 * * 1"

        with patch("backend.scheduling.beat_manager.SessionLocal") as mock_session:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = [schedule]
            mock_session.return_value = mock_db

            result = _build_beat_schedule()
            assert "scheduled:test-uuid" in result
            entry = result["scheduled:test-uuid"]
            assert entry["task"] == "tasks.execute_scheduled_pipeline"
            assert "schedule" in entry
            assert entry["kwargs"] == {"schedule_id": "test-uuid"}

    def test_skips_invalid_cron(self):
        schedule = MagicMock()
        schedule.id = "bad-schedule"
        schedule.cron_expression = "invalid"

        with patch("backend.scheduling.beat_manager.SessionLocal") as mock_session:
            mock_db = MagicMock()
            mock_db.query.return_value.filter.return_value.all.return_value = [schedule]
            mock_session.return_value = mock_db

            result = _build_beat_schedule()
            assert "scheduled:bad-schedule" not in result


class TestRegisterSchedules:
    def test_replaces_beat_schedule(self):
        from backend.celery_app import celery_app
        original = dict(celery_app.conf.beat_schedule)

        with patch("backend.scheduling.beat_manager._build_beat_schedule") as mock_build:
            mock_build.return_value = {"test-task": {"task": "x", "schedule": 60.0}}
            result = register_schedules()

            assert result == {"test-task": {"task": "x", "schedule": 60.0}}
            assert celery_app.conf.beat_schedule == result

        celery_app.conf.beat_schedule = original
