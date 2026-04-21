import type { Node as ReactFlowNode, Edge as ReactFlowEdge } from "@xyflow/react";

export interface UploadedFile {
  id: string;
  original_filename: string;
  row_count: number | null;
  column_count: number;
  columns: string[];
  dtypes: Record<string, string>;
  file_size_bytes: number;
  schema_drift: SchemaDrift | null;
}

export interface UploadUrlRequest {
  filename: string;
  file_size: number;
}

export interface UploadUrlResponse {
  method: "api" | "direct";
  file_id: string;
  upload_url?: string;
  upload_endpoint?: string;
  confirm_endpoint?: string;
}

export interface PipelineRun {
  id: string;
  name: string;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  total_rows_in: number | null;
  total_rows_out: number | null;
  error_message: string | null;
  duration_ms: number | null;
  step_results: StepResult[];
  healing_attempts: HealingAttempt[];
}

export interface StepResult {
  step_name: string;
  step_type: string;
  step_index: number;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "SKIPPED";
  rows_in: number | null;
  rows_out: number | null;
  columns_in: string[];
  columns_out: string[];
  duration_ms: number | null;
  error_message: string | null;
  warnings: string[];
}

export interface HealingAttempt {
  id: string;
  attempt_number: number;
  status:
    | "CREATED"
    | "NON_HEALABLE"
    | "AI_INVALID"
    | "VALIDATION_FAILED"
    | "APPLIED"
    | "FAILED";
  failed_step_name: string | null;
  error_type: string | null;
  error_message: string | null;
  classification_reason: string | null;
  ai_valid: boolean | null;
  ai_error: string | null;
  parser_valid: boolean | null;
  sandbox_passed: boolean | null;
  validation_errors: string[] | null;
  validation_warnings: string[] | null;
  diff_lines: { type?: string; content?: string; [key: string]: unknown }[] | null;
  created_at: string;
  completed_at: string | null;
}

export interface ValidationResult {
  is_valid: boolean;
  errors: ValidationError[];
  warnings: ValidationWarning[];
}

export interface ValidationError {
  step_name: string | null;
  field: string;
  message: string;
  suggestion: string | null;
}

export interface ValidationWarning {
  step_name: string | null;
  message: string;
}

export interface ReactFlowGraph {
  nodes: ReactFlowNode[];
  edges: ReactFlowEdge[];
}

export interface ColumnLineage {
  column_name: string;
  source_file: string;
  source_column: string;
  transformation_chain: TransformationStep[];
  total_steps: number;
}

export interface TransformationStep {
  step_name: string;
  step_type: string;
  detail: string;
}

export interface ImpactAnalysis {
  source_step: string;
  source_column: string;
  affected_steps: string[];
  affected_output_columns: string[];
}

export interface WidgetConfig {
  id: string;
  title: string;
  icon: string;
  visible: boolean;
  gridColumn: string;
  gridRow: string;
  minWidth: number;
  minHeight: number;
  locked: boolean;
}

// Week 2 Types

export interface ColumnDrift {
  column: string;
  drift_type: "added" | "removed" | "type_changed";
  old_value: string | null;
  new_value: string | null;
  severity: "breaking" | "warning" | "info";
}

export interface SchemaDrift {
  has_drift: boolean;
  breaking_changes: number;
  warnings: number;
  drift_items: ColumnDrift[];
}

export interface SchemaDriftReport {
  file_id: string;
  has_drift: boolean;
  drift_items: ColumnDrift[];
  breaking_changes: number;
  warnings: number;
}

export interface StepPlan {
  step_index: number;
  step_name: string;
  step_type: string;
  estimated_rows_in: number | null;
  estimated_rows_out: number | null;
  estimated_columns: string[];
  will_fail: boolean;
  warnings: string[];
}

export interface ExecutionPlan {
  pipeline_name: string;
  total_steps: number;
  estimated_total_duration_ms: number;
  steps: StepPlan[];
  files_read: string[];
  files_written: string[];
  will_succeed: boolean;
}

export interface PipelinePreview {
  step_name: string;
  columns: string[];
  data: Record<string, unknown>[];
  note?: string;
  step_type?: string;
  estimated_rows_in?: number | null;
  estimated_rows_out?: number | null;
}

export type NotificationType = "slack" | "email";

export interface NotificationConfig {
  id: string;
  type: NotificationType;
  config: Record<string, unknown>;
  events: string[];
  is_active: boolean;
  created_at: string | null;
}

export interface PipelineVersion {
  id: string;
  pipeline_name: string;
  version_number: number;
  yaml_config: string;
  created_at: string;
  change_summary: string | null;
}

export interface PipelineDiff {
  version_a: number;
  version_b: number;
  pipeline_name: string;
  has_changes: boolean;
  steps_added: string[];
  steps_removed: string[];
  steps_modified: { step_name: string; changed_fields: string[] }[];
  unified_diff: string;
  change_summary: string;
}

export interface SchemaSnapshot {
  id: string;
  columns: string[];
  dtypes: Record<string, string>;
  row_count: number;
  captured_at: string;
}

export interface AIGeneratePipelineResponse {
  yaml: string;
  valid: boolean;
  attempts: number;
  error: string | null;
}

export interface AIRepairDiffLine {
  type: "added" | "removed" | "unchanged";
  content: string;
}

export interface AIRepairPipelineResponse {
  corrected_yaml: string;
  diff_lines: AIRepairDiffLine[];
  valid: boolean;
  error: string | null;
}

export interface AIColumnAutocompleteResponse {
  suggestion: string | null;
  confidence: number | null;
}

export interface AIColumnBatchAutocompleteResponse {
  suggestions: Record<string, string | null>;
}

export interface AIYamlValidationResponse {
  valid: boolean;
  error: string | null;
  step_count: number;
}
