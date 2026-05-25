from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    UniqueConstraint,
    func,
)

from backend.database import Base
from backend.models._base import PgJSONB, _generate_uuid


class ColumnPolicy(Base):
    __tablename__ = "column_policies"

    id = Column(
        Uuid,
        primary_key=True,
        default=_generate_uuid,
    )
    file_id = Column(
        Uuid,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    column_name = Column(String(500), nullable=False)
    policy = Column(String(20), nullable=False)
    mask_pattern = Column(String(100), nullable=True)
    allowed_roles = Column(
        PgJSONB, nullable=False, server_default="[]"
    )
    created_by = Column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
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
