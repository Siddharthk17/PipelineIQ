from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, ARRAY
from sqlalchemy.sql import text

from backend.database import Base


class ColumnPolicy(Base):
    __tablename__ = "column_policies"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    file_id = Column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
    )
    column_name = Column(String(500), nullable=False)
    policy = Column(String(20), nullable=False)
    mask_pattern = Column(String(100), nullable=True)
    allowed_roles = Column(
        ARRAY(String()), nullable=False, server_default="{}"
    )
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(
        TIMESTAMP(timezone=True),
        server_default=text("NOW()"),
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
