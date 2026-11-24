"""Data contract ORM model — schema expectations for pipeline output."""
import uuid
from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
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

    Integrity (M-008): version numbers are unique per pipeline and at most
    one version is active per pipeline. The partial unique index is
    enforced by the database; writers must deactivate-then-insert in a
    single transaction and retry on IntegrityError.
    """

    __tablename__ = "pipeline_contracts"

    __table_args__ = (
        UniqueConstraint(
            "pipeline_name", "version",
            name="uq_pipeline_contracts_name_version",
        ),
        Index(
            "uq_pipeline_contracts_active_name",
            "pipeline_name",
            unique=True,
            postgresql_where=text("is_active IS TRUE"),
            sqlite_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_generate_uuid)
    pipeline_name: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict | None] = mapped_column(PgJSONB, nullable=True)
    severity: Mapped[ContractSeverity] = mapped_column(
        SQLEnum(ContractSeverity, name="contractseverity",
                values_callable=_enum_values, validate_strings=True),
        nullable=False, default=ContractSeverity.WARN,
        server_default=text("'warn'"))
    consumers: Mapped[list] = mapped_column(
        PgJSONB, nullable=False, default=list, server_default=text("'[]'"))
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class IdempotencyKey(Base):
    """Replay-safe record of a mutating create request (M-062).

    When a client sends ``Idempotency-Key`` with a run/contract create,
    the completed response is stored here keyed by (scope, key, user).
    A retry with the same key returns the stored response instead of
    creating a duplicate resource. The unique constraint makes
    concurrent duplicate submissions collapse to a single resource:
    the loser gets IntegrityError and replays the winner's response.
    """

    __tablename__ = "idempotency_keys"

    __table_args__ = (
        UniqueConstraint(
            "scope", "key", "user_id",
            name="uq_idempotency_keys_scope_key_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=_generate_uuid)
    scope: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # NULL status_code/response_body = claim placeholder for a request still
    # in flight; completed rows always carry both.
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(PgJSONB, nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
