import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { PipelineEditorWidget } from "@/components/widgets/PipelineEditorWidget";
import { runPipeline } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";

vi.mock("@uiw/react-codemirror", () => ({
  default: ({
    value,
    onChange,
  }: {
    value: string;
    onChange: (next: string) => void;
  }) => (
    <textarea
      aria-label="Pipeline YAML editor"
      data-testid="mock-codemirror"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

vi.mock("@/components/pipeline-builder", () => ({
  StepPalette: () => null,
  PipelineCanvas: () => null,
  ConfigPanel: () => null,
}));

vi.mock("@/components/widgets/AIPipelineModals", () => ({
  AIGeneratePipelineModal: () => null,
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({
    user: { id: "test-user", username: "Test", email: "test@example.com", role: "admin" as const, isDemo: false },
    token: "mock-token",
    login: vi.fn(),
    loginAsDemo: vi.fn(),
    logout: vi.fn(),
    isLoading: false,
  }),
}));

vi.mock("@/components/collaboration/CollaboratorPresence", () => ({
  CollaboratorPresence: () => null,
}));

vi.mock("@/hooks/usePipelineEditor", () => ({
  usePipelineEditor: ({
    onYamlTextChange,
  }: {
    onYamlTextChange: (next: string) => void;
  }) => ({
    pipelineName: "my_pipeline",
    setPipelineName: vi.fn(),
    nodes: [],
    edges: [],
    configuringNodeId: null,
    parseError: null,
    toastMessage: null,
    onNodesChange: vi.fn(),
    onEdgesChange: vi.fn(),
    handleConnect: vi.fn(),
    handleDragStart: vi.fn(),
    handleDrop: vi.fn(),
    handleAddStep: vi.fn(),
    handleConfigure: vi.fn(),
    handleConfigClose: vi.fn(),
    handleConfigSave: vi.fn(),
    handleDeleteNode: vi.fn(),
    handleDeleteEdge: vi.fn(),
    handleYamlChange: onYamlTextChange,
    getAvailableColumns: () => [],
    collaborators: [],
    updateCursor: undefined,
    updateSelectedNode: undefined,
    provider: undefined,
    yYaml: undefined,
  }),
}));

vi.mock("@/lib/api", () => ({
  validatePipeline: vi.fn(async () => ({ is_valid: true, errors: [], warnings: [] })),
  runPipeline: vi.fn(async () => ({ run_id: "run-1", status: "PENDING" })),
  getFiles: vi.fn(async () => []),
  getPipelinePlan: vi.fn(async () => null),
  previewPipelineStep: vi.fn(async () => null),
  generatePipelineWithAI: vi.fn(async () => ({ yaml: "", valid: false, attempts: 1, error: "not-used" })),
  autocompleteColumnsBatchWithAI: vi.fn(async () => ({ suggestions: {} })),
}));

function wrapper({ children }: { children: React.ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

describe("PipelineEditorWidget YAML sync", () => {
  beforeEach(() => {
    usePipelineStore.setState({
      lastYamlConfig: "pipeline:\n  name: persisted_pipeline\n  steps: []",
      activeRun: null,
      activeRunId: null,
    });
  });

  it("does not overwrite in-flight edits with stale persisted YAML", async () => {
    render(<PipelineEditorWidget initialMode="yaml" />, { wrapper });

    const editor = screen.getByTestId("mock-codemirror");
    expect(editor).toHaveValue("pipeline:\n  name: persisted_pipeline\n  steps: []");

    fireEvent.change(editor, {
      target: { value: "pipeline:\n  name: edited_pipeline\n  steps: []" },
    });

    await waitFor(() => {
      expect(screen.getByTestId("mock-codemirror")).toHaveValue(
        "pipeline:\n  name: edited_pipeline\n  steps: []",
      );
    });
  });

  it(
    "shows run errors and temporarily disables reruns on client errors",
    async () => {
      vi.mocked(runPipeline).mockRejectedValueOnce({
        status: 400,
        message: "Bad Request",
        detail: {
          error_type: "ValidationError",
          details: [{ loc: ["body", "yaml_config"], msg: "Field required" }],
        },
      });

      render(<PipelineEditorWidget initialMode="yaml" />, { wrapper });

      const runButton = await screen.findByTestId("run-pipeline-btn");
      await waitFor(
        () => {
          expect(runButton).toBeEnabled();
        },
        { timeout: 5000 },
      );

      fireEvent.click(runButton);

      await waitFor(() => {
        expect(screen.getByTestId("run-pipeline-error")).toBeInTheDocument();
      });
      expect(runButton).toBeDisabled();

      await waitFor(
        () => {
          expect(runButton).toBeEnabled();
        },
        { timeout: 7000 },
      );
    },
    12000,
  );

  it("shows structured schema mismatch errors from the run endpoint", async () => {
    vi.mocked(runPipeline).mockRejectedValueOnce({
      status: 400,
      message:
        "rename_columns.mapping: Column 'old_name' was not found in input 'load_sales'. Available columns: ['status']",
      detail: {
        message: "Schema mismatch detected before execution",
        errors: [
          {
            step_name: "rename_columns",
            field: "mapping",
            message:
              "Column 'old_name' was not found in input 'load_sales'. Available columns: ['status']",
          },
        ],
      },
    });

    render(<PipelineEditorWidget initialMode="yaml" />, { wrapper });

    const runButton = await screen.findByTestId("run-pipeline-btn");
    await waitFor(
      () => {
        expect(runButton).toBeEnabled();
      },
      { timeout: 5000 },
    );

    fireEvent.click(runButton);

    await waitFor(() => {
      expect(screen.getByTestId("run-pipeline-error")).toHaveTextContent(
        "rename_columns.mapping: Column 'old_name' was not found",
      );
    });
  });
});
