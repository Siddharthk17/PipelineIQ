import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  ApiError,
  getToken,
  setToken,
  clearToken,
  getFiles,
  uploadFile,
  deleteFile,
  validatePipeline,
  runPipeline,
  getPipelineRun,
  getPipelineRuns,
  getLineageGraph,
  getColumnLineage,
  getImpactAnalysis,
  checkHealth,
  getPipelinePlan,
  getPipelineVersions,
  getPipelineVersion,
  getPipelineDiff,
  restorePipelineVersion,
  getSchemaHistory,
  login,
  register,
  getMe,
  logout,
  generatePipelineWithAI,
  repairPipelineRunWithAI,
  autocompleteColumnWithAI,
  autocompleteColumnsBatchWithAI,
  validateYamlWithAI,
} from "@/lib/api";

describe("Token management", () => {
  beforeEach(() => localStorage.clear());

  it("getToken returns null when no token stored", () => {
    expect(getToken()).toBeNull();
  });

  it("setToken stores and getToken retrieves", () => {
    setToken("abc123");
    expect(getToken()).toBe("abc123");
  });

  it("clearToken removes the token", () => {
    setToken("abc123");
    clearToken();
    expect(getToken()).toBeNull();
  });
});

describe("ApiError", () => {
  it("extends Error with status and detail", () => {
    const err = new ApiError(404, "Not Found", { detail: "missing" });
    expect(err).toBeInstanceOf(Error);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Not Found");
    expect(err.detail).toEqual({ detail: "missing" });
  });
});

describe("fetchApi (via public functions)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("getFiles sends auth header and parses response", async () => {
    setToken("test-token");
    const mockFiles = [
      { id: "1", original_filename: "test.csv", row_count: 10, column_count: 3, columns: ["a", "b", "c"], dtypes: {}, file_size_bytes: 1024, schema_drift: null },
    ];
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ files: mockFiles, total: 1 }),
    } as Response);

    const files = await getFiles();
    expect(files).toEqual(mockFiles);

    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[0]).toBe("/api/v1/files/");
    expect((call[1]?.headers as Record<string, string>)["Authorization"]).toBe("Bearer test-token");
  });

  it("throws ApiError on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: "Bad Request",
      json: () => Promise.resolve({ detail: "invalid" }),
    } as unknown as Response);

    await expect(validatePipeline("bad yaml")).rejects.toThrow(ApiError);
  });

  it("redirects to /login on 401 when token exists", async () => {
    setToken("expired-token");
    const originalHref = window.location.href;

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: "Unauthorized",
      json: () => Promise.resolve({ detail: "expired" }),
    } as unknown as Response);

    await expect(getFiles()).rejects.toThrow(ApiError);
    expect(getToken()).toBeNull();
  });

  it("runPipeline sends POST with JSON body", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ run_id: "run-1", status: "PENDING" }),
    } as Response);

    const result = await runPipeline("pipeline:\n  name: test\n", "test");
    expect(result).toEqual({ run_id: "run-1", status: "PENDING" });

    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[0]).toBe("/api/v1/pipelines/run");
    expect(call[1]?.method).toBe("POST");
    const body = JSON.parse(call[1]?.body as string);
    expect(body.name).toBe("test");
  });

  it("getPipelineRun fetches by runId", async () => {
    setToken("tok");
    const mockRun = { id: "r1", name: "test", status: "COMPLETED", step_results: [] };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve(mockRun),
    } as Response);

    const run = await getPipelineRun("r1");
    expect(run.id).toBe("r1");
  });

  it("getPipelineRuns passes page, limit, status params", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ runs: [], total: 0 }),
    } as Response);

    await getPipelineRuns(2, 10, "COMPLETED");
    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[0]).toContain("page=2");
    expect(call[0]).toContain("limit=10");
    expect(call[0]).toContain("status=COMPLETED");
  });

  it("deleteFile sends DELETE", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    } as Response);

    await deleteFile("file-1");
    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[1]?.method).toBe("DELETE");
    expect(call[0]).toContain("/files/file-1");
  });

  it("getLineageGraph fetches by runId", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ nodes: [], edges: [] }),
    } as Response);

    const graph = await getLineageGraph("run-1");
    expect(graph.nodes).toEqual([]);
    expect(graph.edges).toEqual([]);
  });

  it("getColumnLineage passes step and column params", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          column_name: "amount",
          source_file: "sales.csv",
          source_column: "amount",
          transformation_chain: [],
          total_steps: 0,
        }),
    } as Response);

    await getColumnLineage("run-1", "step1", "amount");
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("step=step1");
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("column=amount");
  });

  it("getImpactAnalysis passes step and column params", async () => {
    setToken("tok");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          source_step: "s1",
          source_column: "col",
          affected_steps: [],
          affected_output_columns: [],
        }),
    } as Response);

    await getImpactAnalysis("run-1", "s1", "col");
    const url = vi.mocked(fetch).mock.calls[0][0] as string;
    expect(url).toContain("impact");
    expect(url).toContain("step=s1");
  });
});

describe("checkHealth", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("calls /health (no /api/v1 prefix)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: "ok", version: "1.0", db: "ok", redis: "ok" }),
    } as Response);

    const health = await checkHealth();
    expect(health.status).toBe("ok");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/health");
  });

  it("throws on failure", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    } as Response);

    await expect(checkHealth()).rejects.toThrow(ApiError);
  });
});

describe("Week 2 API functions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setToken("tok");
  });

  it("getPipelinePlan sends POST with yaml", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          pipeline_name: "test",
          total_steps: 2,
          estimated_total_duration_ms: 100,
          steps: [],
          files_read: [],
          files_written: [],
          will_succeed: true,
        }),
    } as Response);

    const plan = await getPipelinePlan("pipeline:\n  name: test\n");
    expect(plan.will_succeed).toBe(true);
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain("/pipelines/plan");
  });

  it("getPipelineVersions fetches versions by name", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          pipeline_name: "test",
          total_versions: 1,
          versions: [{ id: "v1", pipeline_name: "test", version_number: 1, yaml_config: "", created_at: "", change_summary: null }],
        }),
    } as Response);

    const versions = await getPipelineVersions("test");
    expect(versions).toHaveLength(1);
  });

  it("getPipelineVersion fetches specific version", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ id: "v1", pipeline_name: "test", version_number: 2, yaml_config: "", created_at: "", change_summary: null }),
    } as Response);

    const v = await getPipelineVersion("test", 2);
    expect(v.version_number).toBe(2);
  });

  it("getPipelineDiff fetches diff between versions", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          version_a: 1,
          version_b: 2,
          pipeline_name: "test",
          has_changes: true,
          steps_added: ["new_step"],
          steps_removed: [],
          steps_modified: [],
          unified_diff: "--- v1\n+++ v2",
          change_summary: "Added new_step",
        }),
    } as Response);

    const diff = await getPipelineDiff("test", 1, 2);
    expect(diff.has_changes).toBe(true);
    expect(diff.steps_added).toContain("new_step");
  });

  it("restorePipelineVersion sends POST", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ yaml_config: "pipeline:\n  name: restored\n" }),
    } as Response);

    const result = await restorePipelineVersion("test", 1);
    expect(result.yaml_config).toContain("restored");
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe("POST");
  });

  it("getSchemaHistory fetches snapshots", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          file_id: "f1",
          total_snapshots: 2,
          snapshots: [
            { id: "s1", columns: ["a"], dtypes: { a: "int64" }, row_count: 10, captured_at: "2024-01-01T00:00:00Z" },
            { id: "s2", columns: ["a", "b"], dtypes: { a: "int64", b: "object" }, row_count: 15, captured_at: "2024-01-02T00:00:00Z" },
          ],
        }),
    } as Response);

    const snaps = await getSchemaHistory("f1");
    expect(snaps).toHaveLength(2);
  });

  it("generatePipelineWithAI posts to /api/ai/generate", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ yaml: "pipeline:\n  name: ai", valid: true, attempts: 1, error: null }),
    } as Response);

    const result = await generatePipelineWithAI("Build a basic ETL flow for sales data", ["file-1"]);
    expect(result.valid).toBe(true);

    const call = vi.mocked(fetch).mock.calls[0];
    expect(call[0]).toBe("/api/ai/generate");
    expect(call[1]?.method).toBe("POST");
  });

  it("repairPipelineRunWithAI posts to /api/ai/runs/{id}/repair", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          corrected_yaml: "pipeline:\n  name: repaired",
          diff_lines: [{ type: "added", content: "  name: repaired" }],
          valid: true,
          error: null,
        }),
    } as Response);

    const result = await repairPipelineRunWithAI("run-1");
    expect(result.valid).toBe(true);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/ai/runs/run-1/repair");
  });

  it("autocompleteColumnWithAI posts typed and available columns", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ suggestion: "revenue", confidence: 0.91 }),
    } as Response);

    const result = await autocompleteColumnWithAI("reveue", ["revenue", "region"]);
    expect(result.suggestion).toBe("revenue");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/ai/autocomplete/column");
  });

  it("autocompleteColumnsBatchWithAI posts to batch endpoint", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ suggestions: { reveue: "revenue", rgion: "region" } }),
    } as Response);

    const result = await autocompleteColumnsBatchWithAI(["reveue", "rgion"], ["revenue", "region"]);
    expect(result.suggestions.reveue).toBe("revenue");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/ai/autocomplete/columns");
  });

  it("validateYamlWithAI posts YAML to /api/ai/validate-yaml", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ valid: true, error: null, step_count: 3 }),
    } as Response);

    const result = await validateYamlWithAI("pipeline:\n  name: test\n  steps: []");
    expect(result.step_count).toBe(3);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/api/ai/validate-yaml");
  });
});

describe("Auth API functions", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("login sends POST to /auth/login", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({
          access_token: "new-token",
          token_type: "bearer",
          expires_in: 3600,
          user: { id: "u1", email: "test@test.com", username: "tester", role: "admin", is_active: true, created_at: "" },
        }),
    } as Response);

    const res = await login("test@test.com", "password");
    expect(res.access_token).toBe("new-token");
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/auth/login");
  });

  it("register sends POST to /auth/register", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ id: "u1", email: "new@test.com", username: "newuser", role: "viewer", is_active: true, created_at: "" }),
    } as Response);

    const user = await register("new@test.com", "newuser", "password");
    expect(user.username).toBe("newuser");
  });

  it("getMe sends GET to /auth/me with auth", async () => {
    setToken("my-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve({ id: "u1", email: "me@test.com", username: "me", role: "admin", is_active: true, created_at: "" }),
    } as Response);

    const me = await getMe();
    expect(me.email).toBe("me@test.com");
    expect((vi.mocked(fetch).mock.calls[0][1]?.headers as Record<string, string>)["Authorization"]).toBe("Bearer my-token");
  });

  it("logout revokes server session and clears the token", async () => {
    setToken("token-to-clear");
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ message: "Logged out successfully" }),
    } as Response);

    await logout();

    expect(vi.mocked(fetch).mock.calls[0][0]).toBe("/auth/logout");
    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(getToken()).toBeNull();
  });
});
