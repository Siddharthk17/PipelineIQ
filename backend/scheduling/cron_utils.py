"""
Utilities for working with cron expressions.
Uses `croniter` for validation and next-run calculation.
Uses a lookup table for human-readable descriptions.
"""
from datetime import datetime, timezone
from typing import Optional

try:
    from croniter import croniter
except ImportError:
    raise ImportError("Run: pip install croniter>=1.4.0")


# Validation

def validate_cron(expression: str) -> tuple[bool, str]:
    """
    Validate a cron expression.
    Returns (is_valid, error_message).
    """
    if not expression or not expression.strip():
        return False, "Cron expression must not be empty"

    try:
        is_valid = croniter.is_valid(expression.strip())
        if not is_valid:
            return False, f"Invalid cron expression: '{expression}'"
        return True, ""
    except Exception as e:
        return False, str(e)


def get_next_run_at(expression: str, from_time: datetime = None) -> datetime:
    """
    Get the next scheduled run time after from_time (default: now).
    Returns a timezone-aware datetime in UTC.
    """
    if from_time is None:
        from_time = datetime.now(timezone.utc)

    # croniter works with naive datetimes — strip timezone for calculation
    naive_from = from_time.replace(tzinfo=None)
    cron = croniter(expression.strip(), naive_from)
    next_naive = cron.get_next(datetime)

    # Re-attach UTC timezone
    return next_naive.replace(tzinfo=timezone.utc)


def get_next_n_runs(expression: str, n: int = 5) -> list[datetime]:
    """Get the next N scheduled run times (for the schedule preview UI)."""
    results = []
    from_time = datetime.now(timezone.utc)

    cron = croniter(expression.strip(), from_time.replace(tzinfo=None))
    for _ in range(n):
        next_run = cron.get_next(datetime).replace(tzinfo=timezone.utc)
        results.append(next_run)

    return results


# Human-readable descriptions

# Common patterns → human descriptions
CRON_HUMAN_MAP: dict[str, str] = {
    "0 * * * *":        "every hour",
    "0 */2 * * *":      "every 2 hours",
    "0 */4 * * *":      "every 4 hours",
    "0 */6 * * *":      "every 6 hours",
    "0 */12 * * *":     "every 12 hours",
    "0 0 * * *":        "every day at midnight",
    "0 6 * * *":        "every day at 6:00 AM",
    "0 8 * * *":        "every day at 8:00 AM",
    "0 9 * * *":        "every day at 9:00 AM",
    "0 12 * * *":       "every day at noon",
    "0 18 * * *":       "every day at 6:00 PM",
    "0 0 * * 0":        "every Sunday at midnight",
    "0 9 * * 1":        "every Monday at 9:00 AM",
    "0 6 * * 1":        "every Monday at 6:00 AM",
    "0 9 * * 2":        "every Tuesday at 9:00 AM",
    "0 9 * * 3":        "every Wednesday at 9:00 AM",
    "0 9 * * 4":        "every Thursday at 9:00 AM",
    "0 9 * * 5":        "every Friday at 9:00 AM",
    "0 0 1 * *":        "on the 1st of every month",
    "0 0 1 1 *":        "on January 1st",
    "*/5 * * * *":      "every 5 minutes",
    "*/15 * * * *":     "every 15 minutes",
    "*/30 * * * *":     "every 30 minutes",
}


def cron_to_human(expression: str) -> str:
    """
    Convert a cron expression to a human-readable description.
    Returns the mapped description or a fallback string.
    """
    normalized = expression.strip()

    if normalized in CRON_HUMAN_MAP:
        return CRON_HUMAN_MAP[normalized]

    try:
        parts = normalized.split()
        if len(parts) != 5:
            return f"custom schedule: {normalized}"

        minute, hour, dom, month, dow = parts
        desc = _build_cron_description(minute, hour, dom, month, dow)
        return desc or f"custom schedule: {normalized}"
    except Exception:
        return f"custom schedule: {normalized}"


def _build_cron_description(minute: str, hour: str, dom: str, month: str, dow: str) -> str:
    """Build a human-readable description from cron parts."""
    dow_names = {
        "0": "Sunday", "1": "Monday", "2": "Tuesday",
        "3": "Wednesday", "4": "Thursday", "5": "Friday", "6": "Saturday",
        "7": "Sunday",
    }

    if hour.isdigit() and minute.isdigit():
        h, m = int(hour), int(minute)
        if h == 0 and m == 0:
            time_str = "midnight"
        elif h == 12 and m == 0:
            time_str = "noon"
        else:
            period = "AM" if h < 12 else "PM"
            h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
            time_str = f"{h12}:{m:02d} {period}"
    elif hour.startswith("*/"):
        interval = hour[2:]
        return f"every {interval} hours"
    else:
        time_str = f"at {hour}:{minute}"

    if dow != "*":
        day_name = dow_names.get(dow, f"day {dow}")
        return f"every {day_name} at {time_str}"
    elif dom != "*":
        return f"on the {dom} of every month at {time_str}"
    else:
        return f"every day at {time_str}"


def parse_celery_crontab(expression: str):
    """
    Convert a cron string to a Celery crontab schedule object.
    """
    from celery.schedules import crontab

    parts = expression.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression: {expression}")

    minute, hour, dom, month, dow = parts

    return crontab(
        minute=minute,
        hour=hour,
        day_of_week=dow,
        day_of_month=dom,
        month_of_year=month,
    )
