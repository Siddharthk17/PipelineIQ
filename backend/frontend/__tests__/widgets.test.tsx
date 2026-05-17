import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Mock motion/react to avoid animation issues in tests
vi.mock("motion/react", () => {
  const MotionDiv = React.forwardRef(({ children, ...props }: any, ref: any) => (
    <div ref={ref} {...props}>{children}</div>
  ));
  MotionDiv.displayName = "MotionDiv";

  const MotionSpan = React.forwardRef(({ children, ...props }: any, ref: any) => (
    <span ref={ref} {...props}>{children}</span>
  ));
  MotionSpan.displayName = "MotionSpan";

  return {
    motion: {
      div: MotionDiv,
      span: MotionSpan,
    },
    AnimatePresence: ({ children }: any) => <>{children}</>,
  };
});

// Mock the API module
vi.mock("@/lib/api", () => ({
  getFiles: vi.fn(),
  getPipelineRuns: vi.fn(),
  repairPipelineRunWithAI: vi.fn(),
  uploadFile: vi.fn(),
  deleteFile: vi.fn(),
  getFilePreview: vi.fn(),
  getSchemaHistory: vi.fn(),
}));

vi.mock("@/hooks/usePipelineRun", () => ({
  usePipelineRun: vi.fn(),
}));

import { getFiles, getPipelineRuns, repairPipelineRunWithAI, uploadFile, deleteFile } from "@/lib/api";
import { QuickStatsWidget } from "@/components/widgets/QuickStatsWidget";
import { FileUploadWidget } from "@/components/widgets/FileUploadWidget";
import { RunHistoryWidget } from "@/components/widgets/RunHistoryWidget";
import { FileRegistryWidget } from "@/components/widgets/FileRegistryWidget";
import { RunMonitorWidget } from "@/components/widgets/RunMonitorWidget";
import { usePipelineStore } from "@/store/pipelineStore";

function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("QuickStatsWidget", () => {
  beforeEach(() => {
    vi.mocked(getFiles).mockResolvedValue([
      { id: "1", original_filename: "a.csv", row_count: 10, column_count: 3, columns: [], dtypes: {}, file_size_bytes: 1024, schema_drift: null },
      { id: "2", original_filename: "b.csv", row_count: 20, column_count: 5, columns: [], dtypes: {}, file_size_bytes: 2048, schema_drift: null },
    ]);
    vi.mocked(getPipelineRuns).mockResolvedValue([
      { id: "r1", name: "run1", status: "COMPLETED", created_at: "", started_at: null, completed_at: null, total_rows_in: 10, total_rows_out: 5, error_message: null, duration_ms: 100, step_results: [], healing_attempts: [] },
      { id: "r2", name: "run2", status: "FAILED", created_at: "", started_at: null, completed_at: null, total_rows_in: null, total_rows_out: null, error_message: "err", duration_ms: null, step_results: [], healing_attempts: [] },
      { id: "r3", name: "run3", status: "COMPLETED", created_at: "", started_at: null, completed_at: null, total_rows_in: 20, total_rows_out: 15, error_message: null, duration_ms: 200, step_results: [], healing_attempts: [] },
    ]);
  });

  it("renders 4 stats cards", async () => {
    render(<QuickStatsWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("Total Pipeline Runs")).toBeInTheDocument();
      expect(screen.getByText("Successful Runs")).toBeInTheDocument();
      expect(screen.getByText("Files Uploaded")).toBeInTheDocument();
      expect(screen.getByText("Avg Pipeline Duration")).toBeInTheDocument();
    });
  });

  it("displays correct file count", async () => {
    render(<QuickStatsWidget />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("2")).toBeInTheDocument();
    });
  });
});

describe("FileUploadWidget", () => {
  beforeEach(() => {
    vi.mocked(uploadFile).mockResolvedValue({
      id: "new-file",
      original_filename: "test.csv",
      row_count: 100,
      column_count: 5,
      columns: ["a", "b", "c", "d", "e"],
      dtypes: {},
      file_size_bytes: 5000,
      schema_drift: null,
    });
  });

  it("renders the upload zone", () => {
    render(<FileUploadWidget />, { wrapper });
    expect(screen.getByText(/Drop CSV or JSON here/i)).toBeInTheDocument();
  });

  it("shows file input element", () => {
    render(<FileUploadWidget />, { wrapper });
    const fileInput = document.querySelector("input[type='file']");
    expect(fileInput).toBeInTheDocument();
  });

  it("uploads file on selection", async () => {
    const user = userEvent.setup();
    render(<FileUploadWidget />, { wrapper });

    const file = new File(["col1,col2\n1,2"], "test.csv", { type: "text/csv" });
    const input = document.querySelector("input[type='file']") as HTMLInputElement;

    await user.upload(input, file);

    await waitFor(() => {
      expect(vi.mocked(uploadFile)).toHaveBeenCalled();
      expect(vi.mocked(uploadFile).mock.calls[0][0]).toBeInstanceOf(File);
    });
  });
});

describe("RunHistoryWidget", () => {
  const mockRuns = [
    { id: "r1", name: "pipeline_a", status: "COMPLETED" as const, created_at: "2024-01-01T00:00:00Z", started_at: null, completed_at: null, total_rows_in: 100, total_rows_out: 50, error_message: null, duration_ms: 1500, step_results: [], healing_attempts: [] },
    { id: "r2", name: "pipeline_b", status: "FAILED" as const, created_at: "2024-01-02T00:00:00Z", started_at: null, completed_at: null, total_rows_in: null, total_rows_out: null, error_message: "timeout", duration_ms: null, step_results: [], healing_attempts: [] },
    { id: "r3", name: "pipeline_c", status: "RUNNING" as const, created_at: "2024-01-03T00:00:00Z", started_at: null, completed_at: null, total_rows_in: null, total_rows_out: null, error_message: null, duration_ms: null, step_results: [], healing_attempts: [] },
  ];

  beforeEach(() => {
    vi.mocked(getPipelineRuns).mockResolvedValue(mockRuns);
    vi.mocked(repairPipelineRunWithAI).mockResolvedValue({
      corrected_yaml: "pipeline:\n  name: fixed",
      diff_lines: [{ type: "added", content: "  name: fixed" }],
      valid: true,
      error: null,
    });
  });

  it("renders run history table", async () => {
    render(<RunHistoryWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pipeline_a")).toBeInTheDocument();
      expect(screen.getByText("pipeline_b")).toBeInTheDocument();
      expect(screen.getByText("pipeline_c")).toBeInTheDocument();
    });
  });

  it("shows run status indicators", async () => {
    render(<RunHistoryWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pipeline_a")).toBeInTheDocument();
    });
  });

  it("clicking a run sets active run in pipeline store", async () => {
    const { usePipelineStore } = await import("@/store/pipelineStore");
    const user = userEvent.setup();

    render(<RunHistoryWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pipeline_a")).toBeInTheDocument();
    });

    await user.click(screen.getByText("pipeline_a"));
    expect(usePipelineStore.getState().activeRunId).toBe("r1");
  });

  it("shows a direct quota error message for failed AI repair attempts", async () => {
    const user = userEvent.setup();
    vi.mocked(repairPipelineRunWithAI).mockResolvedValueOnce({
      corrected_yaml: "",
      diff_lines: [],
      valid: false,
      error: "Google Gemini AI quota exhausted. Please try again later.",
    });

    render(<RunHistoryWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pipeline_b")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("repair-pipeline-btn"));

    await waitFor(() => {
      expect(
        screen.getByText("Google Gemini AI quota exhausted. Please try again later.")
      ).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/AI produced YAML but validation still failed/i)
    ).not.toBeInTheDocument();
  });
});

describe("FileRegistryWidget", () => {
  beforeEach(() => {
    vi.mocked(getFiles).mockResolvedValue([
      { id: "f1", original_filename: "sales.csv", row_count: 100, column_count: 4, columns: ["a"], dtypes: {}, file_size_bytes: 2048, schema_drift: null },
      { id: "f2", original_filename: "data.json", row_count: 50, column_count: 3, columns: ["b"], dtypes: {}, file_size_bytes: 1024, schema_drift: null },
    ]);
  });

  it("renders file list", async () => {
    render(<FileRegistryWidget />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("sales.csv")).toBeInTheDocument();
      expect(screen.getByText("data.json")).toBeInTheDocument();
    });
  });

  it("shows row/col counts", async () => {
    render(<FileRegistryWidget />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText("100 rows")).toBeInTheDocument();
      expect(screen.getByText("4 cols")).toBeInTheDocument();
    });
  });

  it("shows empty state when no files", async () => {
    vi.mocked(getFiles).mockResolvedValue([]);
    render(<FileRegistryWidget />, { wrapper });
    await waitFor(() => {
      expect(screen.getByText(/No files yet/i)).toBeInTheDocument();
    });
  });
});

describe("RunMonitorWidget", () => {
  beforeEach(() => {
    usePipelineStore.setState({
      activeRunId: "run-healing",
      activeRun: {
        id: "run-healing",
        name: "healing-run",
        status: "FAILED",
        created_at: "2024-01-01T00:00:00Z",
        started_at: null,
        completed_at: null,
        total_rows_in: 100,
        total_rows_out: 90,
        error_message: "step failed",
        duration_ms: 1200,
        step_results: [],
        healing_attempts: [
          {
            id: "ha-1",
            attempt_number: 1,
            status: "AI_INVALID",
            pipeline_name: "healing-run",
            failed_step: "filter_step",
            error_type: "ColumnNotFoundError",
            error_message: "Column not found",
            old_schema: null,
            new_schema: null,
            removed_columns: [],
            added_columns: [],
            renamed_candidates: [],
            gemini_patch: null,
            sandbox_result: null,
            applied: false,
            confidence: null,
            healed_at: null,
            classification_reason: "Error looks healable",
            diff_lines: [],
            ai_valid: false,
            ai_error: "invalid patch",
            parser_valid: false,
            sandbox_passed: false,
            validation_errors: [],
            validation_warnings: [],
            created_at: "2024-01-01T00:01:00Z",
            completed_at: "2024-01-01T00:01:05Z",
          },
        ],
      },
    });
  });

  it("renders autonomous healing attempt details", () => {
    render(<RunMonitorWidget />, { wrapper });
    expect(screen.getByText("Autonomous Healing Attempts")).toBeInTheDocument();
    expect(screen.getByText("Attempt 1")).toBeInTheDocument();
    expect(screen.getByText("AI_INVALID")).toBeInTheDocument();
    expect(screen.getByText("filter_step")).toBeInTheDocument();
  });

  it("renders the healed banner when the run status is HEALED", () => {
    usePipelineStore.setState({
      activeRunId: "run-healed",
      activeRun: {
        id: "run-healed",
        name: "healed-run",
        status: "HEALED",
        created_at: "2024-01-01T00:00:00Z",
        started_at: null,
        completed_at: null,
        total_rows_in: 100,
        total_rows_out: 95,
        error_message: null,
        duration_ms: 1500,
        step_results: [],
        healing_attempts: [
          {
            id: "ha-2",
            attempt_number: 1,
            status: "APPLIED",
            pipeline_name: "healed-run",
            failed_step: "filter_step",
            error_type: "ColumnNotFoundError",
            error_message: null,
            old_schema: null,
            new_schema: null,
            removed_columns: ["revenue"],
            added_columns: ["rev_usd"],
            renamed_candidates: [],
            gemini_patch: { change_description: "Renamed revenue to rev_usd" },
            sandbox_result: { output_rows: 100 },
            applied: true,
            confidence: 0.93,
            healed_at: "2024-01-01T00:01:05Z",
            classification_reason: "revenue -> rev_usd",
            diff_lines: [],
            ai_valid: true,
            ai_error: null,
            parser_valid: true,
            sandbox_passed: true,
            validation_errors: [],
            validation_warnings: [],
            created_at: "2024-01-01T00:01:00Z",
            completed_at: "2024-01-01T00:01:05Z",
          },
        ],
      },
    });

    render(<RunMonitorWidget />, { wrapper });
    expect(screen.getByTestId("healing-banner-healed")).toBeInTheDocument();
    expect(screen.getByText("Auto-healed successfully")).toBeInTheDocument();
  });
});
