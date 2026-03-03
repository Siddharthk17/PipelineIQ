"""SQLAlchemy ORM models for PipelineIQ.

All models use UUID primary keys, timezone-aware timestamps, and explicit
column length limits. JSON columns use the native JSON type for structured
data storage and querying.
"""

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import List, Optional

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class PipelineStatus(str, PyEnum):
    """Lifecycle states for a pipeline run."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepStatus(str, PyEnum):
    """Lifecycle states for an individual pipeline step."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _generate_uuid() -> str:
    return str(uuid.uuid4())


class PipelineRun(Base):
    """Represents a single execution of a user-defined pipeline.

    Tracks the full lifecycle from PENDING through COMPLETED or FAILED,
    including timing, row counts, error details, and relationships to
    individual step results and the computed lineage graph.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
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

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipeline_runs.id"), nullable=False
    )
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(50), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[StepStatus] = mapped_column(
        SQLEnum(StepStatus), nullable=False, default=StepStatus.PENDING
    )
    rows_in: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    rows_out: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    columns_in: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    columns_out: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    warnings: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
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

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    pipeline_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipeline_runs.id"), nullable=False, unique=True
    )
    graph_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    react_flow_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pipeline_run: Mapped["PipelineRun"] = relationship(
        "PipelineRun", back_populates="lineage_graph"
    )


class UploadedFile(Base):
    """Tracks a user-uploaded data file (CSV or JSON).

    Stores metadata about the file including its original name,
    storage path, row/column counts, and the parsed column list
    for use during pipeline validation.
    """

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_generate_uuid
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list] = mapped_column(JSON, nullable=False)
    dtypes: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
