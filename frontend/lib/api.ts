import { API_V1, API_BASE_URL } from "./constants";
import type {
  UploadedFile,
  ValidationResult,
  PipelineRun,
  ReactFlowGraph,
  ColumnLineage,
  ImpactAnalysis,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    public message: string,
    public detail?: unknown
  ) {
    super(message);
  }
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_V1}${endpoint}`, {
    ...options,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail;
    try {
      detail = await res.json();
    } catch {
      detail = await res.text();
    }
    throw new ApiError(res.status, res.statusText, detail);
  }
  return res.json() as Promise<T>;
}

export async function uploadFile(file: File): Promise<UploadedFile> {
  const formData = new FormData();
  formData.append("file", file);
  return fetchApi<UploadedFile>("/files/upload", {
    method: "POST",
    body: formData,
  });
}

export async function getFiles(): Promise<UploadedFile[]> {
  const data = await fetchApi<{ files: UploadedFile[]; total: number }>("/files/");
  return data.files;
}

export async function getFile(fileId: string): Promise<UploadedFile> {
  return fetchApi<UploadedFile>(`/files/${fileId}`);
}

export async function deleteFile(fileId: string): Promise<void> {
  await fetchApi<void>(`/files/${fileId}`, { method: "DELETE" });
}

export async function getFilePreview(fileId: string): Promise<Record<string, unknown>[]> {
  const data = await fetchApi<{ file_id: string; filename: string; total_rows: number; preview_rows: number; columns: string[]; data: Record<string, unknown>[] }>(`/files/${fileId}/preview`);
  return data.data;
}

export async function validatePipeline(yamlConfig: string): Promise<ValidationResult> {
  return fetchApi<ValidationResult>("/pipelines/validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml_config: yamlConfig }),
  });
}

export async function runPipeline(yamlConfig: string, name?: string): Promise<{ run_id: string; status: string }> {
  return fetchApi<{ run_id: string; status: string }>("/pipelines/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml_config: yamlConfig, name }),
  });
}

export async function getPipelineRun(runId: string): Promise<PipelineRun> {
  return fetchApi<PipelineRun>(`/pipelines/${runId}`);
}

export async function getPipelineRuns(page: number, limit: number, status?: string): Promise<PipelineRun[]> {
  let url = `/pipelines/?page=${page}&limit=${limit}`;
  if (status) url += `&status=${status}`;
  const data = await fetchApi<{ runs: PipelineRun[]; total: number }>(url);
  return data.runs;
}

export async function deletePipeline(runId: string): Promise<void> {
  await fetchApi<void>(`/pipelines/${runId}`, { method: "DELETE" });
}

export async function getLineageGraph(runId: string): Promise<ReactFlowGraph> {
  return fetchApi<ReactFlowGraph>(`/lineage/${runId}`);
}

export async function getColumnLineage(runId: string, step: string, column: string): Promise<ColumnLineage> {
  return fetchApi<ColumnLineage>(`/lineage/${runId}/column?step=${step}&column=${column}`);
}

export async function getImpactAnalysis(runId: string, step: string, column: string): Promise<ImpactAnalysis> {
  return fetchApi<ImpactAnalysis>(`/lineage/${runId}/impact?step=${step}&column=${column}`);
}

export async function checkHealth(): Promise<{ status: string; version: string; db: string; redis: string }> {
  const res = await fetch(`${API_BASE_URL}/health`);
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  return res.json();
}
