import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ReactFlow } from "@xyflow/react";
import { StepNode } from "@/components/pipeline-builder/StepNode";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";

function renderStepNodes(nodes: BuilderNode[]) {
  const edges: BuilderEdge[] = [];
  return render(
    <div style={{ width: 800, height: 400 }}>
      <ReactFlow<BuilderNode, BuilderEdge>
        nodes={nodes}
        edges={edges}
        nodeTypes={{ stepNode: StepNode }}
        fitView
      />
    </div>,
  );
}

function makeNodeData(overrides: Partial<BuilderNode["data"]> & { id?: string; type?: BuilderNode["data"]["type"] } = {}): BuilderNode {
  const id = overrides.id ?? "step_1";
  const stepType = overrides.type ?? "filter";
  return {
    id,
    type: "stepNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      type: stepType,
      config: {},
      backendSupported: true,
      onConfigure: overrides.onConfigure ?? (() => {}),
      onDelete: overrides.onDelete ?? (() => {}),
      ...overrides,
    },
  };
}

describe("StepNode", () => {
  it("renders step name", () => {
    const node = makeNodeData({ id: "my_filter", type: "filter", label: "my_filter" });
    renderStepNodes([node]);
    expect(screen.getByTestId("step-node-my_filter")).toBeInTheDocument();
    expect(screen.getByText("my_filter")).toBeInTheDocument();
  });

  it("renders step type label", () => {
    const node = makeNodeData({ type: "filter" });
    renderStepNodes([node]);
    expect(screen.getByText("Filter")).toBeInTheDocument();
  });

  it("config button triggers onConfigure", () => {
    const onConfigure = vi.fn();
    const node = makeNodeData({ onConfigure });
    renderStepNodes([node]);
    fireEvent.click(screen.getByTestId("config-btn-step_1"));
    expect(onConfigure).toHaveBeenCalledWith("step_1");
  });

  it("delete button triggers onDelete", () => {
    const onDelete = vi.fn();
    const node = makeNodeData({ onDelete });
    renderStepNodes([node]);
    fireEvent.click(screen.getByTestId("delete-btn-step_1"));
    expect(onDelete).toHaveBeenCalledWith("step_1");
  });

  it("shows error badge when validationError is set", () => {
    const node = makeNodeData({ validationError: "Column 'amount' not found" });
    renderStepNodes([node]);
    const badge = screen.getByTestId("error-badge-step_1");
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveTextContent("Column 'amount' not found");
  });

  it("does not show error badge when no validationError", () => {
    const node = makeNodeData();
    renderStepNodes([node]);
    expect(screen.queryByTestId("error-badge-step_1")).toBeNull();
  });

  it("join node has two input handles", () => {
    const node = makeNodeData({ id: "my_join", type: "join", label: "my_join" });
    renderStepNodes([node]);
    expect(screen.getByTestId("handle-my_join-left")).toBeInTheDocument();
    expect(screen.getByTestId("handle-my_join-right")).toBeInTheDocument();
  });

  it("non-join node has single input handle", () => {
    const node = makeNodeData({ type: "filter" });
    renderStepNodes([node]);
    expect(screen.getByTestId("handle-step_1-in")).toBeInTheDocument();
    expect(screen.queryByTestId("handle-step_1-left")).toBeNull();
  });

  it("load node has no input handle (isSource=true)", () => {
    const node = makeNodeData({ id: "my_load", type: "load", label: "my_load" });
    renderStepNodes([node]);
    expect(screen.queryByTestId("handle-my_load-in")).toBeNull();
  });

  it("save node has no output handle (isTerminal=true)", () => {
    const node = makeNodeData({ id: "my_save", type: "save", label: "my_save" });
    renderStepNodes([node]);
    expect(screen.queryByTestId("handle-my_save-out")).toBeNull();
  });

  it("stream_consume node has no input handle (isSource=true)", () => {
    const node = makeNodeData({ id: "my_consume", type: "stream_consume", label: "my_consume" });
    renderStepNodes([node]);
    expect(screen.queryByTestId("handle-my_consume-in")).toBeNull();
  });

  it("stream_publish node has no output handle (isTerminal=true)", () => {
    const node = makeNodeData({ id: "my_publish", type: "stream_publish", label: "my_publish" });
    renderStepNodes([node]);
    expect(screen.queryByTestId("handle-my_publish-out")).toBeNull();
  });

  it("shows schema hint when inferredSchema is provided", () => {
    const node = makeNodeData({
      inferredSchema: ["a", "b", "c"],
      outputSchema: ["a", "b"],
    });
    renderStepNodes([node]);
    const hint = screen.getByTestId("schema-hint-step_1");
    expect(hint).toBeInTheDocument();
    expect(hint).toHaveTextContent("3 → 2 cols");
  });

  it("does not show schema hint when inferredSchema is not provided", () => {
    const node = makeNodeData();
    renderStepNodes([node]);
    expect(screen.queryByTestId("schema-hint-step_1")).toBeNull();
  });
});
