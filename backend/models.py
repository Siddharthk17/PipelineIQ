"""SQLAlchemy ORM models for PipelineIQ.

All models use UUID primary keys, timezone-aware timestamps, and explicit
column length limits. Uses JSONB on PostgreSQL and JSON on SQLite.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from backend.database import Base

# Dialect-aware JSONB: native JSONB on PostgreSQL, JSON fallback on SQLite
PgJSONB = JSONB().with_variant(JSON(), "sqlite")


def _enum_values(enum_class: type[PyEnum]) -> list[str]:
    """Persist Python Enum values (not names) in database enum columns."""
    return [member.value for member in enum_class]


class PipelineStatus(str, PyEnum):
    """Lifecycle states for a pipeline run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    HEALING = "HEALING"
    HEALED = "HEALED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class StepStatus(str, PyEnum):
    """Lifecycle states for an individual pipeline step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class HealingAttemptStatus(str, PyEnum):
    """Lifecycle states for autonomous healing attempts."""

    CREATED = "CREATED"
    NON_HEALABLE = "NON_HEALABLE"
    AI_INVALID = "AI_INVALID"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


def _generate_uuid() -> uuid.UUID:
    return uuid.uuid4()


class PipelineRun(Base):
    """Represents a single execution of a user-defined pipeline.

    Tracks the full lifecycle from PENDING through COMPLETED or FAILED,
    including timing, row counts, error details, and relationships to
    individual step results and the computed lineage graph.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[PipelineStatus] = mapped_column(
        SQLEnum(PipelineStatus), nullable=False, default=PipelineStatus.PENDING
    )
    yaml_config: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    total_rows_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_rows_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    schedule_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("pipeline_schedules.id", ondelete="SET NULL"), nullable=True
    )

    step_results: Mapped[List["StepResult"]] = relationship(
        "StepResult",
        back_populates="pipeline_run",
        order_by="StepResult.step_index",
        cascade="all, delete-orphan",
    )
    lineage_graph: Mapped[Optional["LineageGraph"]] = relationship(
        "LineageGraph",
        back_populates="pipeline_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    healing_attempts: Mapped[List["HealingAttempt"]] = relationship(
        "HealingAttempt",
        back_populates="pipeline_run",
        order_by="HealingAttempt.attempt_number",
        cascade="all, delete-orphan",
    )

    @property
    def duration_ms(self) -> Optional[int]:
        """Calculate total execution duration in milliseconds."""
        if self.started_at and self.completed_at:
            delta = self.completed_at - self.started_at
            return int(delta.total_seconds() * 1000)
        return None


class StepResult(Base):
    """Stores the execution result of a single pipeline step.

    Each step records its input/output row counts, column lists,
    timing, warnings, and any error that occurred during execution.
    """

    __tablename__ = "step_results"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    pipeline_run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        SQLEnum(StepStatus), nullable=False, default=StepStatus.PENDING
    )
    rows_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rows_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    columns_in: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    columns_out: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    warnings: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="step_results"
    )


class LineageGraph(Base):
    """Stores the serialized column-level lineage graph for a pipeline run.

    The graph_data column contains the full NetworkX node-link serialization.
    The react_flow_data column contains the pre-computed React Flow layout
    for instant API responses without server-side recomputation.
    """

    __tablename__ = "lineage_graphs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    pipeline_run_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id"), nullable=False, unique=True
    )
    graph_data: Mapped[dict] = mapped_column(PgJSONB, nullable=False)
    react_flow_data: Mapped[dict] = mapped_column(PgJSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="lineage_graph"
    )


class HealingAttempt(Base):
    """Stores metadata for each autonomous healing attempt of a failed run."""

    __tablename__ = "healing_attempts"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    run_id: Mapped[str] = mapped_column(
        "pipeline_run_id",
        Uuid, ForeignKey("pipeline_runs.id"), nullable=False, index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[HealingAttemptStatus] = mapped_column(
        SQLEnum(HealingAttemptStatus), nullable=False, default=HealingAttemptStatus.CREATED
    )
    pipeline_name: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    failed_step: Mapped[Optional[str]] = mapped_column(
        "failed_step_name", String(255), nullable=True
    )
    error_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    old_schema = mapped_column(PgJSONB, nullable=True)
    new_schema = mapped_column(PgJSONB, nullable=True)
    removed_columns = mapped_column(PgJSONB, nullable=True)
    added_columns = mapped_column(PgJSONB, nullable=True)
    renamed_candidates = mapped_column(PgJSONB, nullable=True)
    gemini_patch = mapped_column(PgJSONB, nullable=True)
    sandbox_result = mapped_column(PgJSONB, nullable=True)
    applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    healed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    classification_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    proposed_yaml: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    diff_lines: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    ai_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    ai_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parser_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    sandbox_passed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    validation_errors: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    validation_warnings: Mapped[Optional[list]] = mapped_column(PgJSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    pipeline_run_id = synonym("run_id")
    failed_step_name = synonym("failed_step")

    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="healing_attempts"
    )


class UploadedFile(Base):
    """Tracks a user-uploaded data file (CSV or JSON).

    Stores metadata about the file including its original name,
    storage path, row/column counts, and the parsed column list
    for use during pipeline validation.
    """

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list] = mapped_column(PgJSONB, nullable=False)
    dtypes: Mapped[dict] = mapped_column(PgJSONB, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    previous_version_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("uploaded_files.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    schema_snapshots: Mapped[List["SchemaSnapshot"]] = relationship(
        "SchemaSnapshot", back_populates="file", cascade="all, delete-orphan"
    )


class SchemaSnapshot(Base):
    """Records the schema of a file at a specific point in time for drift detection."""

    __tablename__ = "schema_snapshots"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    file_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("uploaded_files.id"), nullable=False
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id"), nullable=True
    )
    columns: Mapped[list] = mapped_column(PgJSONB, nullable=False)
    dtypes: Mapped[dict] = mapped_column(PgJSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    file: Mapped["UploadedFile"] = relationship(
        "UploadedFile", back_populates="schema_snapshots"
    )


class PipelineVersion(Base):
    """Stores versioned pipeline YAML configurations for diffing and restoring."""

    __tablename__ = "pipeline_versions"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    pipeline_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_config: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id"), nullable=True
    )
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("pipeline_name", "version_number"),)


class User(Base):
    """Registered user with role-based access control."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    username: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    webhooks = relationship(
        "Webhook", back_populates="user", cascade="all, delete-orphan"
    )


class Webhook(Base):
    """Webhook registration for pipeline event notifications."""

    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    events = mapped_column(
        PgJSONB,
        nullable=False,
        default=lambda: ["pipeline_completed", "pipeline_failed"],
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, server_default="true", default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user = relationship("User", back_populates="webhooks")
    deliveries = relationship(
        "WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan"
    )


class WebhookDelivery(Base):
    """Record of a webhook delivery attempt."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    webhook_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[Optional[str]] = mapped_column(Uuid, nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload = mapped_column(PgJSONB, nullable=False)
    response_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    webhook = relationship("Webhook", back_populates="deliveries")


class AuditLog(Base):
    """Immutable audit log entry for tracking user actions."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    user_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(Uuid, nullable=True)
    details = mapped_column(PgJSONB, nullable=True, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PermissionLevel(str, PyEnum):
    """Permission levels for per-pipeline RBAC."""

    OWNER = "owner"
    RUNNER = "runner"
    VIEWER = "viewer"


class NotificationType(str, PyEnum):
    """Supported notification channel types."""

    SLACK = "slack"
    EMAIL = "email"


class PipelineSchedule(Base):
    """Recurring schedule for automatic pipeline execution via Celery Beat."""

    __tablename__ = "pipeline_schedules"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    pipeline_name: Mapped[str] = mapped_column(String(500), nullable=False)
    yaml_config: Mapped[str] = mapped_column(Text, nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    cron_human: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    total_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    successful_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    healed_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScheduleRun(Base):
    """Tracks history of runs triggered by a pipeline schedule."""

    __tablename__ = "schedule_runs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    schedule_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("pipeline_schedules.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[Optional[str]] = mapped_column(
        Uuid, ForeignKey("pipeline_runs.id", ondelete="SET NULL"), nullable=True
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class NotificationConfig(Base):
    """User notification channel configuration (Slack, email, etc.)."""

    __tablename__ = "notification_configs"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[NotificationType] = mapped_column(
        SQLEnum(
            NotificationType,
            name="notificationtype",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    config = mapped_column(PgJSONB, nullable=False, default=dict)
    events = mapped_column(
        PgJSONB,
        nullable=False,
        default=lambda: ["pipeline_completed", "pipeline_failed"],
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PipelinePermission(Base):
    """Per-pipeline role-based access control entry."""

    __tablename__ = "pipeline_permissions"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    pipeline_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    permission_level: Mapped[PermissionLevel] = mapped_column(
        SQLEnum(
            PermissionLevel,
            name="permissionlevel",
            values_callable=_enum_values,
            validate_strings=True,
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FileProfile(Base):
    """Automatic data profile computed for each uploaded file."""

    __tablename__ = "file_profiles"

    id: Mapped[str] = mapped_column(Uuid, primary_key=True, default=_generate_uuid)
    file_id: Mapped[str] = mapped_column(
        Uuid,
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    profile = mapped_column(PgJSONB, nullable=False, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    col_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completeness_pct: Mapped[Optional[float]] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
