"""Pydantic request and response schemas for the PipelineIQ API.

Every schema has Field descriptors for OpenAPI documentation,
validators for non-obvious constraints, and Config examples
for interactive API documentation.
"""

from datetime import datetime
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class ColumnDriftResponse(BaseModel):
    """A single schema drift item."""

    column: str = Field(..., description="Column name affected")
    drift_type: str = Field(...,
                            description="Type of drift: added, removed, type_changed")
    old_value: str | None = Field(
        None, description="Previous type (for type_changed)")
    new_value: str | None = Field(
        None, description="New type (for type_changed)")
    severity: str = Field(..., description="Severity: breaking, warning, info")


class SchemaDriftResponse(BaseModel):
    """Schema drift report between uploads of the same file."""

    has_drift: bool = Field(..., description="Whether drift was detected")
    breaking_changes: int = Field(0, description="Number of breaking changes")
    warnings: int = Field(0, description="Number of warnings")
    drift_items: list[ColumnDriftResponse] = Field(
        default_factory=list, description="Individual drift items"
    )


class FileUploadResponse(BaseModel):
    """Response returned after a successful file upload."""

    id: str = Field(..., description="Unique identifier for the uploaded file")
    original_filename: str = Field(...,
                                   description="Original filename as uploaded")
    row_count: int = Field(..., description="Number of data rows in the file")
    column_count: int = Field(..., description="Number of columns in the file")
    columns: list[str] = Field(..., description="Ordered list of column names")
    dtypes: dict[str, str] = Field(
        ..., description="Mapping of column name to pandas dtype"
    )
    file_size_bytes: int = Field(..., description="File size in bytes")
    schema_drift: SchemaDriftResponse | None = Field(
        None, description="Schema drift report (null for first upload of a file)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "original_filename": "sales.csv",
                "row_count": 50,
                "column_count": 5,
                "columns": [
                    "order_id",
                    "customer_id",
                    "amount",
                    "status",
                    "date"],
                "dtypes": {
                    "order_id": "int64",
                    "customer_id": "int64",
                    "amount": "float64",
                    "status": "object",
                    "date": "object",
                },
                "file_size_bytes": 2048,
            }}}


class UploadUrlRequest(BaseModel):
    """Request body for upload URL negotiation."""

    filename: str = Field(..., description="Original filename to upload")
    file_size: int = Field(...,
                           description="Client-reported file size in bytes",
                           ge=1)


class UploadUrlResponse(BaseModel):
    """Response for upload URL negotiation."""

    method: str = Field(..., description="Upload method: api or direct")
    file_id: str = Field(..., description="Reserved file identifier")
    upload_url: str | None = Field(
        None, description="Direct upload URL for large files"
    )
    upload_endpoint: str | None = Field(
        None, description="API upload endpoint for small files"
    )
    confirm_endpoint: str | None = Field(
        None, description="Confirmation endpoint after direct upload"
    )


class FileListResponse(BaseModel):
    """Response listing all uploaded files."""

    files: list[FileUploadResponse] = Field(
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
    name: str | None = Field(
        None,
        description="Human-readable name for this run. Defaults to pipeline name from YAML.",
        max_length=255,
    )

    @field_validator("yaml_config")
    @classmethod
    def validate_yaml_parseable(cls, value: str) -> str:
        """Ensure the YAML string is syntactically valid."""
        try:
            yaml.safe_load(value.strip())
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

    run_id: str = Field(...,
                        description="Unique identifier for this pipeline run")
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
            yaml.safe_load(value.strip())
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML syntax: {exc}") from exc
        return value


class ValidationErrorDetail(BaseModel):
    """A single validation error found in a pipeline configuration."""

    step_name: str | None = Field(
        None, description="Name of the step with the error, if applicable"
    )
    field: str = Field(..., description="Field name that has the error")
    message: str = Field(..., description="Human-readable error description")
    suggestion: str | None = Field(
        None, description="Suggested fix for the error"
    )


class ValidationWarningDetail(BaseModel):
    """A non-blocking warning about the pipeline configuration."""

    step_name: str | None = Field(
        None, description="Name of the step with the warning"
    )
    message: str = Field(..., description="Human-readable warning description")


class ValidatePipelineResponse(BaseModel):
    """Response from pipeline validation."""

    is_valid: bool = Field(..., description="Whether the pipeline is valid")
    errors: list[ValidationErrorDetail] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: list[ValidationWarningDetail] = Field(
        default_factory=list, description="List of validation warnings"
    )


class StepResultResponse(BaseModel):
    """Execution result for a single pipeline step."""

    step_name: str = Field(..., description="Name of the step")
    step_type: str = Field(...,
                           description="Type of the step (filter, join, etc.)")
    step_index: int = Field(...,
                            description="Zero-based position in the pipeline")
    status: str = Field(..., description="Step execution status")
    rows_in: int | None = Field(None, description="Number of input rows")
    rows_out: int | None = Field(None, description="Number of output rows")
    columns_in: list[str] | None = Field(
        None, description="Input column names")
    columns_out: list[str] | None = Field(
        None, description="Output column names")
    duration_ms: int | None = Field(
        None, description="Execution time in ms")
    warnings: list[str] | None = Field(
        None, description="Non-fatal warnings")
    error_message: str | None = Field(
        None, description="Error details if failed")
    trace_id: str | None = Field(
        None, description="OpenTelemetry trace ID")
    span_id: str | None = Field(
        None, description="OpenTelemetry span ID")
    started_at: str | None = Field(
        None, description="Step start timestamp (ISO 8601)")
    completed_at: str | None = Field(
        None, description="Step completion timestamp (ISO 8601)")
    engine: str | None = Field(
        None, description="Execution engine (pandas, duckdb)")
    download_url: str | None = Field(
        None, description="Presigned download URL for save step output")
    output_filename: str | None = Field(
        None, description="Output filename for save step")
    output_format: str | None = Field(
        None, description="Output format (csv, json, parquet)")
    output_size_bytes: int | None = Field(
        None, description="Output file size in bytes")


class HealingAttemptResponse(BaseModel):
    """Autonomous healing attempt details for a failed pipeline run."""

    id: str = Field(..., description="Healing attempt ID")
    attempt_number: int = Field(...,
                                description="1-indexed healing attempt number")
    status: str = Field(..., description="Healing attempt status")
    pipeline_name: str | None = Field(
        None, description="Pipeline name for this run")
    failed_step: str | None = Field(
        None, description="Step name where failure was observed")
    error_type: str | None = Field(
        None, description="Failure exception type")
    error_message: str | None = Field(
        None, description="Failure exception message")
    old_schema: dict[str, Any] | None = Field(
        None, description="Schema snapshot captured when the run started"
    )
    new_schema: dict[str, Any] | None = Field(
        None, description="Current schema observed when healing ran"
    )
    removed_columns: list[str] | None = Field(
        None, description="Columns that disappeared between old and new schema"
    )
    added_columns: list[str] | None = Field(
        None, description="Columns that appeared in the new schema"
    )
    renamed_candidates: list[dict[str, Any]] | None = Field(
        None, description="Likely rename pairs detected from the schema diff"
    )
    gemini_patch: dict[str, Any] | None = Field(
        None, description="Structured JSON patch returned by Gemini"
    )
    sandbox_result: dict[str, Any] | None = Field(
        None, description="DuckDB sandbox validation result for the patch"
    )
    applied: bool = Field(
        False, description="Whether the candidate patch was applied")
    confidence: float | None = Field(
        None, description="Gemini confidence score for the selected patch"
    )
    healed_at: datetime | None = Field(
        None, description="When the patch was accepted and applied"
    )
    classification_reason: str | None = Field(
        None, description="Healer classification reason"
    )
    ai_valid: bool | None = Field(
        None, description="AI-reported patch validity")
    ai_error: str | None = Field(
        None, description="AI generation error details")
    parser_valid: bool | None = Field(
        None, description="Whether parser/semantic validation passed"
    )
    sandbox_passed: bool | None = Field(
        None, description="Whether dry-run planner predicted success"
    )
    validation_errors: list[str] | None = Field(
        None, description="Candidate validation errors"
    )
    validation_warnings: list[str] | None = Field(
        None, description="Candidate validation warnings"
    )
    diff_lines: list[dict[str, Any]] | None = Field(
        None, description="Line-level patch diff from AI repair"
    )
    created_at: datetime = Field(..., description="Attempt creation timestamp")
    completed_at: datetime | None = Field(
        None, description="Attempt completion timestamp"
    )


class ContractDefResponse(BaseModel):
    """A data contract definition for a pipeline."""

    id: str = Field(..., description="Contract ID")
    pipeline_name: str = Field(..., description="Pipeline name")
    version: int = Field(..., description="Contract version number")
    yaml_content: str = Field(..., description="Contract YAML content")
    severity: str = Field("warn", description="Breach severity: warn or block")
    consumers: list[str] = Field(default_factory=list, description="Notification targets for breaches")
    is_active: bool = Field(..., description="Whether the contract is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ContractCreateRequest(BaseModel):
    """Request body to create a data contract."""

    yaml_content: str = Field(..., description="Contract YAML content", min_length=1)
    severity: str = Field(default="warn", description="Breach severity: warn or block", pattern="^(warn|block)$")
    consumers: list[str] = Field(default_factory=list, description="Notification targets for breaches")


class ContractUpdateRequest(BaseModel):
    """Request body to update a data contract."""

    yaml_content: str = Field(..., description="Contract YAML content", min_length=1)
    severity: str = Field(default="warn", description="Breach severity: warn or block", pattern="^(warn|block)$")
    consumers: list[str] = Field(default_factory=list, description="Notification targets for breaches")


class ContractListResponse(BaseModel):
    """List of contract definitions for a pipeline."""

    pipeline_name: str = Field(..., description="Pipeline name")
    contracts: list[ContractDefResponse] = Field(
        default_factory=list, description="Contract definitions"
    )
    total: int = Field(..., description="Total number of contracts")


class ContractStatusResponse(BaseModel):
    """Status of a pipeline's data contract against its latest run."""

    pipeline_name: str = Field(..., description="Pipeline name")
    has_contract: bool = Field(..., description="Whether a contract is defined")
    active_contract_id: str | None = Field(None, description="Active contract ID")
    active_contract_version: int | None = Field(
        None, description="Active contract version"
    )
    last_run_id: str | None = Field(None, description="Latest run ID")
    last_run_status: str | None = Field(None, description="Latest run status")
    total_violations: int = Field(default=0, description="Total violations in latest run")


class ContractViolationResponse(BaseModel):
    """A single data contract violation detected during pipeline execution."""

    column: str = Field(..., description="Column name")
    rule: str = Field(..., description="Violated rule (dtype, not_null, unique, etc.)")
    severity: str = Field(..., description="Severity level: error or warning")
    message: str = Field(..., description="Human-readable violation description")
    actual: str | None = Field(None, description="Actual value observed")
    expected: str | None = Field(None, description="Expected value per contract")


class PipelineRunResponse(BaseModel):
    """Full details of a pipeline run including all step results."""

    id: str = Field(..., description="Pipeline run ID")
    name: str = Field(..., description="Human-readable pipeline name")
    status: str = Field(..., description="Current pipeline status")
    created_at: datetime = Field(..., description="When the run was created")
    started_at: datetime | None = Field(
        None, description="When execution started"
    )
    completed_at: datetime | None = Field(
        None, description="When execution finished"
    )
    duration_ms: int | None = Field(
        None, description="Total execution time in ms"
    )
    total_rows_in: int | None = Field(None, description="Total input rows")
    total_rows_out: int | None = Field(
        None, description="Total output rows")
    error_message: str | None = Field(None, description="Error if failed")
    trace_id: str | None = Field(
        None, description="OpenTelemetry trace ID for distributed tracing")
    step_results: list[StepResultResponse] = Field(
        default_factory=list, description="Ordered list of step results"
    )
    healing_attempts: list[HealingAttemptResponse] = Field(
        default_factory=list,
        description="Ordered healing attempts for this run",
    )


class PipelineRunListResponse(BaseModel):
    """Response listing pipeline runs."""

    runs: list[PipelineRunResponse] = Field(...,
                                            description="List of pipeline runs")
    total: int = Field(..., description="Total number of runs")


class ReactFlowNodeResponse(BaseModel):
    """A single node in the React Flow lineage visualization."""

    id: str = Field(..., description="Unique node identifier")
    type: str = Field(...,
                      description="Node type: sourceFile, stepNode, columnNode, outputFile")
    data: dict[str, Any] = Field(  # Any needed: React Flow data is polymorphic
        ..., description="Node display data (label, metadata)"
    )
    position: dict[str, int] = Field(
        ..., description="Node position as {x, y} coordinates"
    )


class ReactFlowEdgeResponse(BaseModel):
    """A single edge in the React Flow lineage visualization."""

    id: str = Field(..., description="Unique edge identifier")
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    animated: bool = Field(False, description="Whether the edge is animated")
    style: dict[str, str] | None = Field(
        None, description="Edge styling (e.g., stroke color for join keys)"
    )


class LineageGraphResponse(BaseModel):
    """Complete React Flow lineage graph for a pipeline run."""

    nodes: list[ReactFlowNodeResponse] = Field(...,
                                               description="All graph nodes")
    edges: list[ReactFlowEdgeResponse] = Field(...,
                                               description="All graph edges")


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
    transformation_chain: list[TransformationStepResponse] = Field(
        ..., description="Ordered list of transformations applied"
    )
    total_steps: int = Field(
        ..., description="Total number of transformation steps"
    )


class ImpactAnalysisResponse(BaseModel):
    """Forward impact analysis for a column — what downstream outputs it affects."""

    source_step: str = Field(...,
                             description="Step containing the source column")
    source_column: str = Field(..., description="The column being analyzed")
    affected_steps: list[str] = Field(
        ..., description="All downstream steps that use this column"
    )
    affected_output_columns: list[str] = Field(
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
    details: dict[str, Any] | None = Field(  # Any needed: error context varies
        None, description="Additional structured error context"
    )
    request_id: str | None = Field(
        None, description="Request ID for log tracing"
    )


class WasmModuleExport(BaseModel):
    """A single exported function from a Wasm module."""

    name: str = Field(..., description="Exported function name")
    params: list[str] = Field(
        default_factory=list, description="Parameter types (e.g., ['f64', 'f64'])"
    )
    result: str | None = Field(
        None, description="Return type (e.g., 'f64')")


class WasmModuleUploadResponse(BaseModel):
    """Response after uploading a Wasm module."""

    id: str = Field(..., description="Module ID")
    name: str = Field(..., description="Module name (unique)")
    description: str | None = Field(None, description="Module description")
    file_size_bytes: int = Field(..., description="Module size in bytes")
    sha256_hash: str = Field(..., description="SHA256 hash of the .wasm binary")
    exports: list[WasmModuleExport] = Field(
        ..., description="Exported functions with signatures"
    )
    imports: list[str] = Field(
        default_factory=list,
        description="Imported modules (must be empty for sandbox)",
    )
    fuel_budget: int = Field(..., description="CPU fuel budget per step")
    is_active: bool = Field(..., description="Whether module is active")
    created_at: datetime = Field(..., description="Upload timestamp")


class WasmModuleListResponse(BaseModel):
    """Response listing all registered Wasm modules."""

    modules: list[WasmModuleUploadResponse] = Field(
        ..., description="List of registered Wasm modules"
    )
    total: int = Field(..., description="Total number of modules")


class WasmModuleValidateResponse(BaseModel):
    """Response from Wasm module validation."""

    is_valid: bool = Field(..., description="Whether the module is valid")
    exports: list[WasmModuleExport] = Field(
        default_factory=list, description="Exported functions"
    )
    imports: list[str] = Field(
        default_factory=list, description="Imported modules"
    )
    errors: list[str] = Field(
        default_factory=list, description="Validation errors"
    )
    warnings: list[str] = Field(
        default_factory=list, description="Validation warnings"
    )
