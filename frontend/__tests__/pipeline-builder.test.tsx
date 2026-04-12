import React from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ReactFlow, type Connection } from "@xyflow/react";
import yaml from "js-yaml";
import { StepNode } from "@/components/pipeline-builder/StepNode";
import { topologicalSort } from "@/lib/topologicalSort";
import { validateConnectionCandidate } from "@/hooks/usePipelineEditor";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";
import { graphToYAML, yamlToGraph } from "@/lib/yamlGraphSync";

describe("topologicalSort", () => {
  it("returns node ids in dependency order", () => {
    const nodes: BuilderNode[] = [
      { id: "load", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "load", type: "load", config: {}, backendSupported: true } },
      { id: "filter", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "filter", type: "filter", config: {}, backendSupported: true } },
      { id: "save", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "save", type: "save", config: {}, backendSupported: true } },
    ];
    const edges: BuilderEdge[] = [
      { id: "e-load-filter", source: "load", target: "filter" },
      { id: "e-filter-save", source: "filter", target: "save" },
    ];

    expect(topologicalSort(nodes, edges)).toEqual(["load", "filter", "save"]);
  });

  it("throws for cyclic graphs", () => {
    const nodes: BuilderNode[] = [
      { id: "a", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "a", type: "filter", config: {}, backendSupported: true } },
      { id: "b", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "b", type: "sort", config: {}, backendSupported: true } },
    ];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-a", source: "b", target: "a" },
    ];

    expect(() => topologicalSort(nodes, edges)).toThrow(/cycle/i);
  });
});

describe("yaml graph sync", () => {
  it("round-trips yaml through graph conversion", () => {
    const inputYaml = `
pipeline:
  name: sales_pipeline
  steps:
    - name: load_sales
      type: load
      file_id: file_1
    - name: delivered_only
      type: filter
      input: load_sales
      column: status
      operator: equals
      value: delivered
    - name: save_output
      type: save
      input: delivered_only
      filename: output_report
`.trim();

    const graph = yamlToGraph(inputYaml);
    expect(graph.pipelineName).toBe("sales_pipeline");
    expect(graph.nodes).toHaveLength(3);
    expect(graph.edges).toHaveLength(2);

    const outputYaml = graphToYAML(graph);
    const parsed = yaml.load(outputYaml) as {
      pipeline: { name: string; steps: Array<Record<string, unknown>> };
    };
    const steps = parsed.pipeline.steps;
    expect(parsed.pipeline.name).toBe("sales_pipeline");
    expect(steps.map((step) => step.type)).toEqual(["load", "filter", "save"]);
  });

  it("preserves join left/right handles when parsing yaml", () => {
    const inputYaml = `
pipeline:
  name: join_test
  steps:
    - name: left_source
      type: load
      file_id: file_left
    - name: right_source
      type: load
      file_id: file_right
    - name: merged
      type: join
      left: left_source
      right: right_source
      on: id
      how: inner
`.trim();

    const graph = yamlToGraph(inputYaml);
    const joinNode = graph.nodes.find((node) => node.data.type === "join");
    const joinEdges = graph.edges.filter((edge) => edge.target === joinNode?.id);

    expect(joinEdges).toHaveLength(2);
    expect(joinEdges.map((edge) => edge.targetHandle).sort()).toEqual(["left", "right"]);
  });
});

describe("validateConnectionCandidate", () => {
  const makeConnection = (source: string, target: string, targetHandle?: string): Connection => ({
    source,
    target,
    sourceHandle: "output",
    targetHandle: targetHandle ?? null,
  });

  const baseNodes: BuilderNode[] = [
    { id: "load", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "load", type: "load", config: {}, backendSupported: true } },
    { id: "filter", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "filter", type: "filter", config: {}, backendSupported: true } },
    { id: "sort", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "sort", type: "sort", config: {}, backendSupported: true } },
    { id: "join", type: "stepNode", position: { x: 0, y: 0 }, data: { label: "join", type: "join", config: {}, backendSupported: true } },
  ];

  it("accepts a valid non-cyclic connection", () => {
    const result = validateConnectionCandidate(makeConnection("load", "filter"), baseNodes, []);
    expect(result.valid).toBe(true);
    expect(result.targetHandle).toBe("input");
  });

  it("rejects duplicate connections", () => {
    const edges: BuilderEdge[] = [{ id: "e-load-filter-input", source: "load", target: "filter", targetHandle: "input" }];
    const result = validateConnectionCandidate(makeConnection("load", "filter"), baseNodes, edges);
    expect(result.valid).toBe(false);
  });

  it("rejects connections that create a cycle", () => {
    const edges: BuilderEdge[] = [{ id: "e-filter-sort", source: "filter", target: "sort", targetHandle: "input" }];
    const result = validateConnectionCandidate(makeConnection("sort", "filter"), baseNodes, edges);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("cycle");
  });

  it("enforces join left/right input capacity", () => {
    const edges: BuilderEdge[] = [
      { id: "e-load-join-left", source: "load", target: "join", targetHandle: "left" },
      { id: "e-filter-join-right", source: "filter", target: "join", targetHandle: "right" },
    ];
    const result = validateConnectionCandidate(makeConnection("sort", "join"), baseNodes, edges);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("two inputs");
  });
});

describe("StepNode component", () => {
  it("renders actions and triggers callbacks", async () => {
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
      <div style={{ width: 500, height: 260 }}>
        <ReactFlow<BuilderNode, BuilderEdge> nodes={nodes} edges={edges} nodeTypes={{ stepNode: StepNode }} fitView />
      </div>,
    );

    fireEvent.click(screen.getByText("Configure"));
    expect(onConfigure).toHaveBeenCalledWith("step_1");

    fireEvent.click(screen.getByText("Delete"));
    expect(onDelete).toHaveBeenCalledWith("step_1");
  });
});
