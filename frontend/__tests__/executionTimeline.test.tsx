import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import React from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── Motion stub ──────────────────────────────────────────────────────────────
vi.mock("motion/react", () => {
  const MotionDiv = React.forwardRef(({ children, ...props }: any, ref: any) => (
    <div ref={ref} {...props}>
      {children}
    </div>
  ));
  MotionDiv.displayName = "MotionDiv";
  return {
    motion: { div: MotionDiv },
    AnimatePresence: ({ children }: any) => <>{children}</>,
  };
});

// ── API stub ─────────────────────────────────────────────────────────────────
vi.mock("@/lib/api", () => ({
  fetchApi: vi.fn(),
}));

// ── usePipelineRun stub (SSE not needed in unit tests) ────────────────────
vi.mock("@/hooks/usePipelineRun", () => ({
  usePipelineRun: vi.fn(),
}));

import { fetchApi } from "@/lib/api";
import { ExecutionTimelineWidget } from "@/components/widgets/ExecutionTimelineWidget";
import { usePipelineStore } from "@/store/pipelineStore";
import type { RunTimingResponse, StepResult } from "@/lib/types";

// ── Test wrapper ─────────────────────────────────────────────────────────────
function wrapper({ children }: { children: React.ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ── Fixtures ──────────────────────────────────────────────────────────────────
const TRACE_ID = "aabbccddeeff00112233445566778899";

const MOCK_TIMING: RunTimingResponse = {
  run_id: "run-abc",
  status: "COMPLETED",
  total_duration_ms: 1500,
  steps: [
    {
      step_index: 0,
      step_name: "load_sales",
      step_type: "load",
      status: "COMPLETED",
      engine: null,
      rows_in: 0,
      rows_out: 100,
      duration_ms: 200,
      started_at: "2024-01-01T00:00:00Z",
      completed_at: "2024-01-01T00:00:01Z",
      trace_id: TRACE_ID,
      span_id: "aabbccdd00000001",
    },
    {
      step_index: 1,
      step_name: "filter_active",
      step_type: "filter",
      status: "COMPLETED",
      engine: null,
      rows_in: 100,
      rows_out: 72,
      duration_ms: 80,
      started_at: "2024-01-01T00:00:01Z",
      completed_at: "2024-01-01T00:00:02Z",
      trace_id: TRACE_ID,
      span_id: "aabbccdd00000002",
    },
  ],
};

function makeStep(overrides: Partial<StepResult> = {}): StepResult {
  return {
    step_name: "load_sales",
    step_type: "load",
    step_index: 0,
    status: "COMPLETED",
    rows_in: 0,
    rows_out: 100,
    columns_in: [],
    columns_out: [],
    duration_ms: 200,
    error_message: null,
    warnings: [],
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────
describe("ExecutionTimelineWidget", () => {
  beforeEach(() => {
    vi.mocked(fetchApi).mockResolvedValue(MOCK_TIMING);
    // Reset store to clean state
    usePipelineStore.setState({ activeRunId: null, activeRun: null } as any);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  // ── Empty / no-selection state ──────────────────────────────────────────────
  it("shows 'No Run Selected' empty state when no run is active", () => {
    render(<ExecutionTimelineWidget />, { wrapper });
    expect(screen.getByText("No Run Selected")).toBeInTheDocument();
  });

  it("does not call the API when no run is selected", () => {
    render(<ExecutionTimelineWidget />, { wrapper });
    expect(vi.mocked(fetchApi)).not.toHaveBeenCalled();
  });

  // ── Happy path ──────────────────────────────────────────────────────────────
  it("fetches timing data when a run is selected", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledWith("/pipelines/run-abc/timing"),
    );
  });

  it("renders step names after fetch", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("load_sales")).toBeInTheDocument();
      expect(screen.getByText("filter_active")).toBeInTheDocument();
    });
  });

  it("renders step types after fetch", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("load")).toBeInTheDocument();
      expect(screen.getByText("filter")).toBeInTheDocument();
    });
  });

  it("displays total duration in the header", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    // 1500 ms → "1.5s total"
    await waitFor(() =>
      expect(screen.getByText("1.5s total")).toBeInTheDocument(),
    );
  });

  // ── Trace ID display ────────────────────────────────────────────────────────
  it("shows trace IDs truncated to 12 chars + ellipsis in each step row", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    const expected = TRACE_ID.slice(0, 12) + "…";
    await waitFor(() => {
      const cells = screen.getAllByText(expected);
      expect(cells.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("shows nothing in the trace column when trace_id is null", async () => {
    const noTraceTiming: RunTimingResponse = {
      ...MOCK_TIMING,
      steps: [{ ...MOCK_TIMING.steps[0], trace_id: null }],
    };
    vi.mocked(fetchApi).mockResolvedValue(noTraceTiming);
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("load_sales")).toBeInTheDocument(),
    );
    // No truncated trace ID should appear
    expect(screen.queryByText(/…$/)).not.toBeInTheDocument();
  });

  // ── Empty steps ─────────────────────────────────────────────────────────────
  it("shows 'No timing data available yet.' when steps array is empty", async () => {
    vi.mocked(fetchApi).mockResolvedValue({ ...MOCK_TIMING, steps: [] });
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(
        screen.getByText("No timing data available yet."),
      ).toBeInTheDocument(),
    );
  });

  // ── Error state ─────────────────────────────────────────────────────────────
  it("shows error message when the API call fails", async () => {
    vi.mocked(fetchApi).mockRejectedValue(new Error("Network error"));
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("Network error")).toBeInTheDocument(),
    );
  });

  it("shows generic error message for non-Error rejections", async () => {
    vi.mocked(fetchApi).mockRejectedValue("boom");
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText("Failed to load timing data")).toBeInTheDocument(),
    );
  });

  // ── SSE-driven refetch (Gap 4 contract) ────────────────────────────────────
  it("refetches timing when a new step completes via SSE (stepsDone increments)", async () => {
    usePipelineStore.setState({
      activeRunId: "run-abc",
      activeRun: {
        id: "run-abc",
        name: "test-pipeline",
        status: "RUNNING",
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        total_rows_in: null,
        total_rows_out: null,
        error_message: null,
        duration_ms: null,
        step_results: [],
        healing_attempts: [],
      },
    } as any);

    render(<ExecutionTimelineWidget />, { wrapper });
    // Initial fetch
    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledTimes(1),
    );

    // Simulate SSE step_completed arriving → usePipelineRun patches the store
    act(() => {
      usePipelineStore.setState((state: any) => ({
        activeRun: state.activeRun
          ? {
              ...state.activeRun,
              step_results: [makeStep({ status: "COMPLETED" })],
            }
          : null,
      }));
    });

    // Widget must re-fetch because stepsDone changed 0 → 1
    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledTimes(2),
    );
  });

  it("refetches when the run reaches a terminal status via SSE", async () => {
    usePipelineStore.setState({
      activeRunId: "run-abc",
      activeRun: {
        id: "run-abc",
        name: "test-pipeline",
        status: "RUNNING",
        created_at: new Date().toISOString(),
        started_at: null,
        completed_at: null,
        total_rows_in: null,
        total_rows_out: null,
        error_message: null,
        duration_ms: null,
        step_results: [],
        healing_attempts: [],
      },
    } as any);

    render(<ExecutionTimelineWidget />, { wrapper });
    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledTimes(1),
    );

    act(() => {
      usePipelineStore.setState((state: any) => ({
        activeRun: state.activeRun
          ? { ...state.activeRun, status: "COMPLETED" }
          : null,
      }));
    });

    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledTimes(2),
    );
  });

  // ── Run change ───────────────────────────────────────────────────────────────
  it("clears timing and re-fetches when the active run changes", async () => {
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledWith("/pipelines/run-abc/timing"),
    );

    act(() => {
      usePipelineStore.setState({ activeRunId: "run-xyz", activeRun: null } as any);
    });

    await waitFor(() =>
      expect(vi.mocked(fetchApi)).toHaveBeenCalledWith("/pipelines/run-xyz/timing"),
    );
  });

  // ── Engine display tests ────────────────────────────────────────────────────
  it("shows engine badge when engine is present", async () => {
    const engineTiming: RunTimingResponse = {
      ...MOCK_TIMING,
      steps: [{ ...MOCK_TIMING.steps[0], engine: "duckdb" }],
    };
    vi.mocked(fetchApi).mockResolvedValue(engineTiming);
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("duckdb")).toBeInTheDocument();
    });
  });

  it("shows dash when engine is null", async () => {
    const noEngineTiming: RunTimingResponse = {
      ...MOCK_TIMING,
      steps: [{ ...MOCK_TIMING.steps[0], engine: null }],
    };
    vi.mocked(fetchApi).mockResolvedValue(noEngineTiming);
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("—")).toBeInTheDocument();
    });
  });

  it("shows pandas engine badge for pandas-routed steps", async () => {
    const pandasTiming: RunTimingResponse = {
      ...MOCK_TIMING,
      steps: [{ ...MOCK_TIMING.steps[0], engine: "pandas" }],
    };
    vi.mocked(fetchApi).mockResolvedValue(pandasTiming);
    usePipelineStore.setState({ activeRunId: "run-abc", activeRun: null } as any);
    render(<ExecutionTimelineWidget />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pandas")).toBeInTheDocument();
    });
  });
});
