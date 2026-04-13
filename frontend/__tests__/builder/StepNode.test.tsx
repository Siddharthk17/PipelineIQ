import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ReactFlow } from "@xyflow/react";
import { StepNode } from "@/components/pipeline-builder/StepNode";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";

describe("StepNode", () => {
  it("renders controls and invokes callbacks", () => {
    const onConfigure = vi.fn();
    const onDelete = vi.fn();

    const nodes: BuilderNode[] = [
      {
        id: "step_1",
        type: "stepNode",
        position: { x: 0, y: 0 },
        data: {
          label: "Load_1",
          type: "load",
          config: { file_id: "" },
          backendSupported: true,
          onConfigure,
          onDelete,
        },
      },
    ];
    const edges: BuilderEdge[] = [];

    render(
      <div style={{ width: 500, height: 280 }}>
        <ReactFlow<BuilderNode, BuilderEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={{ stepNode: StepNode }}
          fitView
        />
      </div>,
    );

    expect(screen.getByTestId("step-node-step_1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("config-btn-step_1"));
    expect(onConfigure).toHaveBeenCalledWith("step_1");

    fireEvent.click(screen.getByTestId("delete-btn-step_1"));
    expect(onDelete).toHaveBeenCalledWith("step_1");
  });
});
