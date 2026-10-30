import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from backend.database import Base
from backend.models._base import _generate_uuid

_PgArray = ARRAY(Text())
_AllowedRoles = _PgArray.with_variant(JSON(), "sqlite")


class ColumnPolicy(Base):
    __tablename__ = "column_policies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=_generate_uuid,
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    column_name: Mapped[str] = mapped_column(String(500), nullable=False)
    policy: Mapped[str] = mapped_column(String(20), nullable=False)
    mask_pattern: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    allowed_roles: Mapped[list[str]] = mapped_column(
        _AllowedRoles,
        nullable=False,
        server_default="{}",
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "policy IN ('redacted', 'masked')",
            name="ck_column_policies_policy",
        ),
        UniqueConstraint(
            "file_id", "column_name", name="uq_column_policies_file_column"
        ),
    )
