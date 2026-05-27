"""Contract violation record ORM model — persistent breach tracking."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models._base import (
    Base,
    _generate_uuid,
)


class ContractViolationRecord(Base):
    """Persistent record of a data contract violation detected during a pipeline run.

    Each violation maps to a single rule (dtype, not_null, unique, min_value, etc.)
    that was checked against a step's output columns. Links back to both the
    parent PipelineRun and the specific StepResult where the violation occurred.
    """

    __tablename__ = "contract_violations"

    id: Mapped[str] = mapped_column(
        Uuid, primary_key=True, default=_generate_uuid)
    run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    column: Mapped[str] = mapped_column(String(255), nullable=False)
    rule: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actual: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index("ix_contract_violations_run_step", "run_id", "step_name"),
        Index("ix_contract_violations_severity", "run_id", "severity"),
    )
