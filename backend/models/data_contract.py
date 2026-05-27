"""Data contract ORM model — schema expectations for pipeline output."""

from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.models._base import (
    Base,
    PgJSONB,
    _enum_values,
    _generate_uuid,
)


class ContractSeverity(str, PyEnum):
    """Breach handling severity for data contracts."""

    WARN = "warn"
    BLOCK = "block"


class PipelineContract(Base):
    """A data contract definition for a pipeline.

    Stores YAML-based schema expectations (column types, constraints, nullability)
    that are checked against pipeline output data during or after execution.
    A pipeline may have multiple contract versions (similar to PipelineVersion).

    severity controls breach handling:
      warn  = log breach + alert, run stays COMPLETED
      block = log breach + alert, run -> CONTRACT_VIOLATION, downstream blocked
    """

    __tablename__ = "pipeline_contracts"

    id: Mapped[str] = mapped_column(
        Uuid, primary_key=True, default=_generate_uuid)
    pipeline_name: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[Optional[dict]] = mapped_column(PgJSONB, nullable=True)
    severity: Mapped[ContractSeverity] = mapped_column(
        SQLEnum(ContractSeverity, name="contractseverity",
                values_callable=_enum_values, validate_strings=True),
        nullable=False, default=ContractSeverity.WARN,
        server_default="'warn'")
    consumers: Mapped[list] = mapped_column(
        PgJSONB, nullable=False, default=list, server_default="'[]'")
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
