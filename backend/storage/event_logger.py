"""Fire-and-forget storage event logger for the Arrow data bus.

Records every tier write, read, eviction, and cleanup to the storage_events
table for analytics and debugging. Inserts use a short connection timeout —
a failed log never blocks or fails the calling operation.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from sqlalchemy import create_engine, text

from backend.config import settings

logger = logging.getLogger(__name__)

_log_lock = threading.Lock()
_event_engine = create_engine(
    settings.DATABASE_WRITE_URL,
    connect_args={"connect_timeout": 2},
    pool_pre_ping=False,
    pool_size=1,
)


def log_storage_event(
    *,
    event_type: str,
    tier: str,
    payload_bytes: Optional[int] = None,
    duration_ms: Optional[float] = None,
    run_id: Optional[str] = None,
    step_name: Optional[str] = None,
    object_name: Optional[str] = None,
) -> None:
    try:
        with _log_lock:
            with _event_engine.connect() as conn:
                conn.execute(
                    text("""
                        INSERT INTO storage_events
                            (run_id, step_name, event_type, tier,
                             payload_bytes, duration_ms, object_name)
                        VALUES
                            (:run_id, :step_name, :event_type, :tier,
                             :payload_bytes, :duration_ms, :object_name)
                    """),
                    {
                        "run_id":        run_id,
                        "step_name":     step_name,
                        "event_type":    event_type,
                        "tier":          tier,
                        "payload_bytes": payload_bytes,
                        "duration_ms":   int(duration_ms) if duration_ms else None,
                        "object_name":   object_name,
                    },
                )
                conn.commit()
    except Exception:
        logger.debug("Storage event log insert failed (non-fatal)", exc_info=True)
