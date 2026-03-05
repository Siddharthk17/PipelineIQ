import { API_V1, API_BASE_URL } from "./constants";
import type {
  UploadedFile,
  ValidationResult,
  PipelineRun,
  ReactFlowGraph,
  ColumnLineage,
  ImpactAnalysis,
  ExecutionPlan,
  PipelineVersion,
  PipelineDiff,
  SchemaSnapshot,
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

// ── Token management ───────────────────────────────────────────────

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("pipelineiq_token");
}

export function setToken(token: string): void {
  localStorage.setItem("pipelineiq_token", token);
}

export function clearToken(): void {
  localStorage.removeItem("pipelineiq_token");
}

// ── Auth types ─────────────────────────────────────────────────────

export interface AuthUser {
  id: string;
  email: string;
  username: string;
  role: string;
  is_active: boolean;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

// ── Core fetch with auth ───────────────────────────────────────────

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string> || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_V1}${endpoint}`, {
    ...options,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 401 && token) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
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

// Week 2 API functions

export async function getPipelinePlan(yamlConfig: string): Promise<ExecutionPlan> {
  return fetchApi<ExecutionPlan>("/pipelines/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ yaml_config: yamlConfig }),
  });
}

export async function getPipelineVersions(pipelineName: string): Promise<PipelineVersion[]> {
  const data = await fetchApi<{ pipeline_name: string; total_versions: number; versions: PipelineVersion[] }>(`/versions/${pipelineName}`);
  return data.versions;
}

export async function getPipelineVersion(name: string, version: number): Promise<PipelineVersion> {
  return fetchApi<PipelineVersion>(`/versions/${name}/${version}`);
}

export async function getPipelineDiff(name: string, v1: number, v2: number): Promise<PipelineDiff> {
  return fetchApi<PipelineDiff>(`/versions/${name}/diff/${v1}/${v2}`);
}

export async function restorePipelineVersion(name: string, version: number): Promise<{ yaml_config: string }> {
  return fetchApi<{ yaml_config: string }>(`/versions/${name}/restore/${version}`, {
    method: "POST",
  });
}

export async function getSchemaHistory(fileId: string): Promise<SchemaSnapshot[]> {
  const data = await fetchApi<{ file_id: string; total_snapshots: number; snapshots: SchemaSnapshot[] }>(`/files/${fileId}/schema/history`);
  return data.snapshots;
}

// ── Auth API functions ─────────────────────────────────────────────

async function fetchAuth<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string> || {}),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  const res = await fetch(`${API_BASE_URL}${endpoint}`, {
    ...options,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    if (res.status === 401 && token) {
      clearToken();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
    }
    let detail;
    try { detail = await res.json(); } catch { detail = await res.text(); }
    throw new ApiError(res.status, res.statusText, detail);
  }
  return res.json() as Promise<T>;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  return fetchAuth<LoginResponse>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
}

export async function register(email: string, username: string, password: string): Promise<AuthUser> {
  return fetchAuth<AuthUser>("/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, username, password }),
  });
}

export async function getMe(): Promise<AuthUser> {
  return fetchAuth<AuthUser>("/auth/me");
}

export function logout(): void {
  clearToken();
}
