import type { Node as ReactFlowNode, Edge as ReactFlowEdge } from "@xyflow/react";

export interface UploadedFile {
  id: string;
  original_filename: string;
  row_count: number | null;
  column_count: number;
  columns: string[];
  dtypes: Record<string, string>;
  file_size_bytes: number;
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
