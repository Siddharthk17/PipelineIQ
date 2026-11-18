"""Per-tenant topic access control for the streaming API (M-006).

Contract shared with the streaming daemon (``backend/tasks/streaming_pipeline.py``,
owned by a sibling agent): both sides gate topic access through
:func:`check_topic_access` so that a user can only list/inspect topics backing
their own pipeline runs. Deny by default; admins may access any topic.

Ownership is derived from :class:`StreamingStats` rows (written at daemon
startup with the consume topic) joined to the owning :class:`PipelineRun`.
Topics with no Stats row attributable to the caller are denied — even if the
broker topic exists — so one tenant can never read another tenant's payloads.
"""

from __future__ import annotations

import logging
import uuid as uuid_module

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.models import PipelineRun, StreamingStats, User

logger = logging.getLogger(__name__)

__all__ = ["base_topic", "check_topic_access", "get_accessible_topics"]

UserLike = User | str | uuid_module.UUID


def base_topic(topic: str) -> str:
    """Strip a trailing ``.dlq`` suffix to get the underlying data topic."""
    topic = (topic or "").strip()
    return topic[:-4] if topic.endswith(".dlq") else topic


def _user_id(user: UserLike) -> object:
    uid = getattr(user, "id", user)
    return uid


def _is_admin(user: UserLike) -> bool:
    return getattr(user, "role", None) == "admin"


def _owns_topic(db: Session, uid: object, topic: str) -> bool:
    base = base_topic(topic)
    if not base:
        return False
    clauses = [StreamingStats.topic == base]
    if topic != base:
        clauses.append(StreamingStats.topic == topic)
    row = (
        db.query(StreamingStats)
        .join(PipelineRun, StreamingStats.run_id == PipelineRun.id)
        .filter(PipelineRun.user_id == uid)
        .filter(or_(*clauses))
        .first()
    )
    return row is not None


def get_accessible_topics(user: UserLike, db: Session) -> set[str]:
    """Return base topic names owned by ``user`` via their streaming runs."""
    uid = _user_id(user)
    rows = (
        db.query(StreamingStats.topic)
        .join(PipelineRun, StreamingStats.run_id == PipelineRun.id)
        .filter(PipelineRun.user_id == uid)
        .all()
    )
    owned: set[str] = set()
    for (name,) in rows:
        if name:
            owned.add(base_topic(name))
    return owned


def check_topic_access(
    user: UserLike,
    topic: str,
    db: Session | None = None,
) -> bool:
    """Return True iff ``user`` may access ``topic`` (or its ``.dlq`` pair).

    Admins may access any topic. Everyone else must own a streaming run
    whose Stats row names the topic; anything else is denied. When ``db``
    is omitted a short-lived session is opened so daemon-side callers can
    use the two-argument form ``check_topic_access(user, topic)``.
    """
    if _is_admin(user):
        return True
    if not topic or not base_topic(topic):
        return False
    if db is not None:
        return _owns_topic(db, _user_id(user), topic)
    from backend.database import SessionLocal

    session = SessionLocal()
    try:
        return _owns_topic(session, _user_id(user), topic)
    except Exception:
        logger.exception("Topic access check failed; denying access")
        return False
    finally:
        session.close()
