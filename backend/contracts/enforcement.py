"""Worker-side contract-breach enforcement helpers (M-076).

A contract breach with severity=block pauses downstream schedules whose
pipeline name appears in the breaching contract's ``consumers`` list.
Pipeline identity is (owner, name): matching by bare pipeline name lets
one tenant's breach pause another tenant's schedules.

These helpers scope every match to schedules owned by the breaching
contract's owner, consistent with the API-side validation
(:func:`backend.api.contracts._validate_consumers`) that only lets a
contract name pipelines its creator owns.
"""

import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.models import PipelineSchedule
from backend.utils.time_utils import utcnow

logger = logging.getLogger(__name__)


def find_matching_schedules(
    db: Session,
    *,
    owner_id,
    consumers: list[str] | None,
) -> list:
    """Return active schedules owned by ``owner_id`` named in ``consumers``.

    Matching is scoped to (owner, pipeline_name): schedules owned by any
    other user are never returned, even when the pipeline name matches.
    """
    if not consumers:
        return []
    names = [c.strip() for c in consumers if isinstance(c, str) and c.strip()]
    if not names:
        return []
    return (
        db.query(PipelineSchedule)
        .filter(
            PipelineSchedule.user_id == owner_id,
            PipelineSchedule.pipeline_name.in_(names),
            PipelineSchedule.is_active.is_(True),
        )
        .all()
    )


def block_downstream_schedules(
    db: Session,
    *,
    owner_id,
    breached_pipeline_name: str,
    consumers: list[str] | None,
) -> int:
    """Pause the owner's downstream schedules named in ``consumers``.

    Pushes ``next_run_at`` forward by 1 hour and marks the schedules
    BLOCKED_BY_CONTRACT so engineers can investigate before dependents
    run against bad data. Returns the number of schedules blocked.
    Only schedules owned by ``owner_id`` are affected (M-076).
    """
    matching = find_matching_schedules(db, owner_id=owner_id, consumers=consumers)
    if not matching:
        return 0

    blocked_count = 0
    for schedule in matching:
        schedule.next_run_at = utcnow() + timedelta(hours=1)
        schedule.last_run_status = "BLOCKED_BY_CONTRACT"
        blocked_count += 1
        logger.info(
            "Blocked downstream schedule '%s' (owner=%s, breached='%s')",
            schedule.pipeline_name,
            owner_id,
            breached_pipeline_name,
        )
    db.commit()
    return blocked_count
