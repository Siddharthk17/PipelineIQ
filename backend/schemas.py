"""Pydantic request and response schemas for the PipelineIQ API.

Every schema has Field descriptors for OpenAPI documentation,
validators for non-obvious constraints, and Config examples
for interactive API documentation.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator


class ColumnDriftResponse(BaseModel):
    """A single schema drift item."""

    column: str = Field(..., description="Column name affected")
    drift_type: str = Field(..., description="Type of drift: added, removed, type_changed")
    old_value: Optional[str] = Field(None, description="Previous type (for type_changed)")
    new_value: Optional[str] = Field(None, description="New type (for type_changed)")
    severity: str = Field(..., description="Severity: breaking, warning, info")


class SchemaDriftResponse(BaseModel):
    """Schema drift report between uploads of the same file."""

    has_drift: bool = Field(..., description="Whether drift was detected")
    breaking_changes: int = Field(0, description="Number of breaking changes")
    warnings: int = Field(0, description="Number of warnings")
    drift_items: List[ColumnDriftResponse] = Field(
        default_factory=list, description="Individual drift items"
    )


class FileUploadResponse(BaseModel):
    """Response returned after a successful file upload."""

    id: str = Field(..., description="Unique identifier for the uploaded file")
    original_filename: str = Field(..., description="Original filename as uploaded")
    row_count: int = Field(..., description="Number of data rows in the file")
    column_count: int = Field(..., description="Number of columns in the file")
    columns: List[str] = Field(..., description="Ordered list of column names")
    dtypes: Dict[str, str] = Field(
        ..., description="Mapping of column name to pandas dtype"
    )
    file_size_bytes: int = Field(..., description="File size in bytes")
    schema_drift: Optional[SchemaDriftResponse] = Field(
        None, description="Schema drift report (null for first upload of a file)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "original_filename": "sales.csv",
                "row_count": 50,
                "column_count": 5,
                "columns": ["order_id", "customer_id", "amount", "status", "date"],
                "dtypes": {
                    "order_id": "int64",
                    "customer_id": "int64",
                    "amount": "float64",
                    "status": "object",
                    "date": "object",
                },
                "file_size_bytes": 2048,
            }
        }
    }


class UploadUrlRequest(BaseModel):
    """Request body for upload URL negotiation."""

    filename: str = Field(..., description="Original filename to upload")
    file_size: int = Field(..., description="Client-reported file size in bytes", ge=1)


class UploadUrlResponse(BaseModel):
    """Response for upload URL negotiation."""

    method: str = Field(..., description="Upload method: api or direct")
    file_id: str = Field(..., description="Reserved file identifier")
    upload_url: Optional[str] = Field(
        None, description="Direct upload URL for large files"
    )
    upload_endpoint: Optional[str] = Field(
        None, description="API upload endpoint for small files"
    )
    confirm_endpoint: Optional[str] = Field(
        None, description="Confirmation endpoint after direct upload"
    )


class FileListResponse(BaseModel):
    """Response listing all uploaded files."""

    files: List[FileUploadResponse] = Field(
        ..., description="List of all uploaded files"
    )
    total: int = Field(..., description="Total number of uploaded files")


class RunPipelineRequest(BaseModel):
    """Request body to start a new pipeline execution."""

    yaml_config: str = Field(
        ...,
        description="Complete YAML pipeline configuration string",
        min_length=10,
    )
    name: Optional[str] = Field(
        None,
        description="Human-readable name for this run. Defaults to pipeline name from YAML.",
        max_length=255,
    )

    @field_validator("yaml_config")
    @classmethod
    def validate_yaml_parseable(cls, value: str) -> str:
        """Ensure the YAML string is syntactically valid."""
        try:
            yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML syntax: {exc}") from exc
        return value

    model_config = {
        "json_schema_extra": {
            "example": {
                "yaml_config": (
                    "pipeline:\n"
                    "  name: example_pipeline\n"
                    "  steps:\n"
                    "    - name: load_data\n"
                    "      type: load\n"
                    "      file_id: abc-123\n"
                ),
                "name": "My First Pipeline Run",
            }
        }
    }


class RunPipelineResponse(BaseModel):
    """Response returned immediately after queueing a pipeline run."""

    run_id: str = Field(..., description="Unique identifier for this pipeline run")
    status: str = Field(..., description="Initial status (always PENDING)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "run_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
                "status": "PENDING",
            }
        }
    }


class ValidatePipelineRequest(BaseModel):
    """Request body to validate a pipeline configuration without executing it."""

    yaml_config: str = Field(
        ...,
        description="Complete YAML pipeline configuration string",
        min_length=10,
    )

    @field_validator("yaml_config")
    @classmethod
    def validate_yaml_parseable(cls, value: str) -> str:
        """Ensure the YAML string is syntactically valid."""
        try:
            yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML syntax: {exc}") from exc
        return value


class ValidationErrorDetail(BaseModel):
    """A single validation error found in a pipeline configuration."""

    step_name: Optional[str] = Field(
        None, description="Name of the step with the error, if applicable"
    )
    field: str = Field(..., description="Field name that has the error")
    message: str = Field(..., description="Human-readable error description")
    suggestion: Optional[str] = Field(
        None, description="Suggested fix for the error"
    )


class ValidationWarningDetail(BaseModel):
    """A non-blocking warning about the pipeline configuration."""

    step_name: Optional[str] = Field(
        None, description="Name of the step with the warning"
    )
    message: str = Field(..., description="Human-readable warning description")


class ValidatePipelineResponse(BaseModel):
    """Response from pipeline validation."""

    is_valid: bool = Field(..., description="Whether the pipeline is valid")
    errors: List[ValidationErrorDetail] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[ValidationWarningDetail] = Field(
        default_factory=list, description="List of validation warnings"
    )


class StepResultResponse(BaseModel):
    """Execution result for a single pipeline step."""

    step_name: str = Field(..., description="Name of the step")
    step_type: str = Field(..., description="Type of the step (filter, join, etc.)")
    step_index: int = Field(..., description="Zero-based position in the pipeline")
    status: str = Field(..., description="Step execution status")
    rows_in: Optional[int] = Field(None, description="Number of input rows")
    rows_out: Optional[int] = Field(None, description="Number of output rows")
    columns_in: Optional[List[str]] = Field(None, description="Input column names")
    columns_out: Optional[List[str]] = Field(None, description="Output column names")
    duration_ms: Optional[int] = Field(None, description="Execution time in ms")
    warnings: Optional[List[str]] = Field(None, description="Non-fatal warnings")
    error_message: Optional[str] = Field(None, description="Error details if failed")


class PipelineRunResponse(BaseModel):
    """Full details of a pipeline run including all step results."""

    id: str = Field(..., description="Pipeline run ID")
    name: str = Field(..., description="Human-readable pipeline name")
    status: str = Field(..., description="Current pipeline status")
    created_at: datetime = Field(..., description="When the run was created")
    started_at: Optional[datetime] = Field(
        None, description="When execution started"
    )
    completed_at: Optional[datetime] = Field(
        None, description="When execution finished"
    )
    duration_ms: Optional[int] = Field(
        None, description="Total execution time in ms"
    )
    total_rows_in: Optional[int] = Field(None, description="Total input rows")
    total_rows_out: Optional[int] = Field(None, description="Total output rows")
    error_message: Optional[str] = Field(None, description="Error if failed")
    step_results: List[StepResultResponse] = Field(
        default_factory=list, description="Ordered list of step results"
    )


class PipelineRunListResponse(BaseModel):
    """Response listing pipeline runs."""

    runs: List[PipelineRunResponse] = Field(..., description="List of pipeline runs")
    total: int = Field(..., description="Total number of runs")


class ReactFlowNodeResponse(BaseModel):
    """A single node in the React Flow lineage visualization."""

    id: str = Field(..., description="Unique node identifier")
    type: str = Field(
        ..., description="Node type: sourceFile, stepNode, columnNode, outputFile"
    )
    data: Dict[str, Any] = Field(  # Any needed: React Flow data is polymorphic
        ..., description="Node display data (label, metadata)"
    )
    position: Dict[str, int] = Field(
        ..., description="Node position as {x, y} coordinates"
    )


class ReactFlowEdgeResponse(BaseModel):
    """A single edge in the React Flow lineage visualization."""

    id: str = Field(..., description="Unique edge identifier")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    animated: bool = Field(False, description="Whether the edge is animated")
    style: Optional[Dict[str, str]] = Field(
        None, description="Edge styling (e.g., stroke color for join keys)"
    )


class LineageGraphResponse(BaseModel):
    """Complete React Flow lineage graph for a pipeline run."""

    nodes: List[ReactFlowNodeResponse] = Field(..., description="All graph nodes")
    edges: List[ReactFlowEdgeResponse] = Field(..., description="All graph edges")


class TransformationStepResponse(BaseModel):
    """A single step in a column's transformation history."""

    step_name: str = Field(..., description="Name of the transformation step")
    step_type: str = Field(..., description="Type of the step")
    detail: str = Field(
        ..., description="Human-readable description of the transformation"
    )


class ColumnLineageResponse(BaseModel):
    """Complete ancestry trace for a single output column."""

    column_name: str = Field(..., description="The queried column name")
    source_file: str = Field(
        ..., description="Original source file the column came from"
    )
    source_column: str = Field(
        ..., description="Original column name in the source file"
    )
    transformation_chain: List[TransformationStepResponse] = Field(
        ..., description="Ordered list of transformations applied"
    )
    total_steps: int = Field(
        ..., description="Total number of transformation steps"
    )


class ImpactAnalysisResponse(BaseModel):
    """Forward impact analysis for a column — what downstream outputs it affects."""

    source_step: str = Field(..., description="Step containing the source column")
    source_column: str = Field(..., description="The column being analyzed")
    affected_steps: List[str] = Field(
        ..., description="All downstream steps that use this column"
    )
    affected_output_columns: List[str] = Field(
        ..., description="All output columns derived from this column"
    )


class HealthResponse(BaseModel):
    """Health check response with service connectivity status."""

    status: str = Field(..., description="Overall health status")
    version: str = Field(..., description="Application version")
    db: str = Field(..., description="Database connectivity status")
    redis: str = Field(..., description="Redis connectivity status")


class ErrorResponse(BaseModel):
    """Structured error response for API errors."""

    error_type: str = Field(..., description="Machine-readable error type")
    message: str = Field(..., description="Human-readable error message")
    details: Optional[Dict[str, Any]] = Field(  # Any needed: error context varies
        None, description="Additional structured error context"
    )
    request_id: Optional[str] = Field(
        None, description="Request ID for log tracing"
    )
