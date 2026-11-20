"""Tests for cron validation and next-run calculation."""
from datetime import UTC, datetime

import pytest

from backend.scheduling.cron_utils import (
    cron_to_human,
    get_next_n_runs,
    get_next_run_at,
    parse_celery_crontab,
    validate_cron,
)


class TestValidateCron:
    def test_valid_daily_cron(self):
        is_valid, err = validate_cron("0 6 * * *")
        assert is_valid is True
        assert err == ""

    def test_valid_weekly_cron(self):
        is_valid, _err = validate_cron("0 9 * * 1")
        assert is_valid is True

    def test_valid_hourly_cron(self):
        is_valid, _err = validate_cron("0 * * * *")
        assert is_valid is True

    def test_valid_every_minute_cron(self):
        is_valid, _err = validate_cron("* * * * *")
        assert is_valid is True

    def test_valid_custom_interval(self):
        is_valid, _err = validate_cron("0 */4 * * *")
        assert is_valid is True

    def test_invalid_cron_too_few_parts(self):
        is_valid, err = validate_cron("0 6 * *")
        assert is_valid is False
        assert len(err) > 0

    def test_invalid_cron_bad_hour(self):
        is_valid, _err = validate_cron("0 25 * * *")
        assert is_valid is False

    def test_empty_cron_invalid(self):
        is_valid, _err = validate_cron("")
        assert is_valid is False

    def test_cron_with_extra_whitespace_valid(self):
        is_valid, _err = validate_cron("  0 6 * * *  ")
        assert is_valid is True

    def test_invalid_cron_returns_error_message(self):
        is_valid, err = validate_cron("not-a-cron")
        assert is_valid is False
        assert len(err) > 5


class TestGetNextRunAt:
    def test_next_run_is_in_future(self):
        now = datetime.now(UTC)
        next_run = get_next_run_at("0 6 * * *")
        assert next_run > now

    def test_next_run_is_timezone_aware(self):
        next_run = get_next_run_at("0 6 * * *")
        assert next_run.tzinfo is not None

    def test_next_run_respects_from_time(self):
        from_time = datetime(2024, 1, 1, 5, 0, 0, tzinfo=UTC)
        next_run = get_next_run_at("0 6 * * *", from_time=from_time)
        assert next_run.hour == 6
        assert next_run.date() == from_time.date()

    def test_next_n_runs_returns_n_items(self):
        runs = get_next_n_runs("0 6 * * *", n=5)
        assert len(runs) == 5

    def test_next_n_runs_are_ordered(self):
        runs = get_next_n_runs("0 6 * * *", n=5)
        for i in range(1, len(runs)):
            assert runs[i] > runs[i-1]


class TestCronToHuman:
    def test_daily_6am(self):
        result = cron_to_human("0 6 * * *")
        assert "6" in result.lower() or "6:00" in result.lower()

    def test_every_monday_9am(self):
        result = cron_to_human("0 9 * * 1")
        assert "monday" in result.lower()

    def test_every_hour(self):
        result = cron_to_human("0 * * * *")
        assert "hour" in result.lower() or "every" in result.lower()

    def test_known_patterns_have_nice_descriptions(self):
        from backend.scheduling.cron_utils import CRON_HUMAN_MAP
        for cron, description in list(CRON_HUMAN_MAP.items())[:5]:
            result = cron_to_human(cron)
            assert result == description

    def test_unknown_pattern_returns_fallback(self):
        result = cron_to_human("37 14 15 3 *")
        assert len(result) > 0


class TestParseCeleryCrontab:
    def test_daily_cron_parses(self):
        crontab = parse_celery_crontab("0 6 * * *")
        assert crontab is not None

    def test_weekly_cron_parses(self):
        crontab = parse_celery_crontab("0 9 * * 1")
        assert crontab is not None

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError):
            parse_celery_crontab("0 6 * *")


class TestTimezoneAwareScheduling:
    """M-117: IANA timezone per schedule, default UTC."""

    def test_default_is_utc(self):
        from backend.scheduling.cron_utils import resolve_timezone
        assert str(resolve_timezone(None)) == "UTC"
        assert str(resolve_timezone("")) == "UTC"
        nxt = get_next_run_at("0 6 * * *")
        assert str(nxt.tzinfo) == "UTC"

    def test_named_timezone_returned_in_zone(self):
        nxt = get_next_run_at("0 6 * * *", tz="America/New_York")
        assert str(nxt.tzinfo) == "America/New_York"

    def test_same_instant_different_zones(self):
        from datetime import UTC as _UTC
        utc_run = get_next_run_at("0 12 * * *", tz="UTC")
        ny_run = get_next_run_at("0 12 * * *", tz="America/New_York")
        # Noon wall-time in NY is not noon UTC — zones shift the instant.
        assert utc_run.astimezone(_UTC) != ny_run.astimezone(_UTC)

    def test_unknown_timezone_raises(self):
        with pytest.raises(ValueError):
            get_next_run_at("0 6 * * *", tz="Mars/Olympus")

    def test_preview_respects_timezone(self):
        runs = get_next_n_runs("0 6 * * *", n=3, tz="Asia/Kathmandu")
        assert len(runs) == 3
        assert all(str(r.tzinfo) == "Asia/Kathmandu" for r in runs)

    def test_celery_crontab_carries_timezone(self):
        crontab = parse_celery_crontab("0 6 * * *", timezone="Asia/Kathmandu")
        assert crontab.tz is not None
        assert str(crontab.tz) == "Asia/Kathmandu"
        utc_crontab = parse_celery_crontab("0 6 * * *")
        assert str(utc_crontab.tz) == "UTC"
