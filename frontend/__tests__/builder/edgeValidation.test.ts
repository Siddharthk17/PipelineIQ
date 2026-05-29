import { describe, expect, it } from "vitest";
import type { Connection } from "@xyflow/react";
import { removeEdgeById, validateConnectionCandidate } from "@/hooks/usePipelineEditor";
import { STEP_DEFINITIONS } from "@/lib/stepDefinitions";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";

function makeConnection(source: string, target: string, targetHandle?: string): Connection {
  return {
    source,
    target,
    sourceHandle: "output",
    targetHandle: targetHandle ?? null,
  };
}

function makeNode(id: string, type: BuilderNode["data"]["type"]): BuilderNode {
  return {
    id,
    type: "stepNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      type,
      config: {},
      backendSupported: true,
    },
  };
}

describe("validateConnectionCandidate", () => {
  const nodes: BuilderNode[] = [
    makeNode("load", "load"),
    makeNode("filter", "filter"),
    makeNode("sort", "sort"),
    makeNode("join", "join"),
    makeNode("save", "save"),
    makeNode("stream_consume", "stream_consume"),
    makeNode("stream_publish", "stream_publish"),
  ];

  it("accepts valid non-cyclic connections", () => {
    const result = validateConnectionCandidate(makeConnection("load", "filter"), nodes, []);
    expect(result.valid).toBe(true);
    expect(result.targetHandle).toBe("input");
  });

  it("rejects duplicate connections", () => {
    const edges: BuilderEdge[] = [
      { id: "e-load-filter-input", source: "load", target: "filter", targetHandle: "input" },
    ];
    const result = validateConnectionCandidate(makeConnection("load", "filter"), nodes, edges);
    expect(result.valid).toBe(false);
  });

  it("rejects cycles", () => {
    const edges: BuilderEdge[] = [{ id: "e-filter-sort", source: "filter", target: "sort" }];
    const result = validateConnectionCandidate(makeConnection("sort", "filter"), nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("cycle");
  });

  it("enforces join input capacity", () => {
    const edges: BuilderEdge[] = [
      { id: "e-load-join-left", source: "load", target: "join", targetHandle: "left" },
      { id: "e-filter-join-right", source: "filter", target: "join", targetHandle: "right" },
    ];
    const result = validateConnectionCandidate(makeConnection("sort", "join"), nodes, edges);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("two inputs");
  });

  it("rejects edges from save steps", () => {
    const result = validateConnectionCandidate(makeConnection("save", "sort"), nodes, []);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("terminal");
  });

  it("rejects edges to source steps", () => {
    const result = validateConnectionCandidate(makeConnection("filter", "load"), nodes, []);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("source step");
  });

  it("rejects edges to stream_consume (isSource=true)", () => {
    const result = validateConnectionCandidate(makeConnection("filter", "stream_consume"), nodes, []);
    expect(result.valid).toBe(false);
  });

  it("rejects edges from stream_publish (isTerminal=true)", () => {
    const result = validateConnectionCandidate(makeConnection("stream_publish", "filter"), nodes, []);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("terminal");
  });

  it("rejects self-connections", () => {
    const result = validateConnectionCandidate(makeConnection("filter", "filter"), nodes, []);
    expect(result.valid).toBe(false);
    expect(result.message).toContain("itself");
  });
});

describe("removeEdgeById", () => {
  it("removes only the targeted edge id", () => {
    const edges: BuilderEdge[] = [
      { id: "e-1", source: "load", target: "filter" },
      { id: "e-2", source: "filter", target: "save" },
      { id: "e-3", source: "load", target: "save" },
    ];

    const nextEdges = removeEdgeById(edges, "e-2");

    expect(nextEdges).toHaveLength(2);
    expect(nextEdges.map((edge) => edge.id)).toEqual(["e-1", "e-3"]);
  });

  it("returns same array when edge not found", () => {
    const edges: BuilderEdge[] = [
      { id: "e-1", source: "load", target: "filter" },
    ];
    const nextEdges = removeEdgeById(edges, "nonexistent");
    expect(nextEdges).toHaveLength(1);
  });
});

describe("STEP_DEFINITIONS properties", () => {
  it("load is a source step", () => {
    expect(STEP_DEFINITIONS.load.isSource).toBe(true);
    expect(STEP_DEFINITIONS.load.isTerminal).toBe(false);
    expect(STEP_DEFINITIONS.load.maxInputs).toBe(0);
    expect(STEP_DEFINITIONS.load.minInputs).toBe(0);
  });

  it("save is a terminal step", () => {
    expect(STEP_DEFINITIONS.save.isTerminal).toBe(true);
    expect(STEP_DEFINITIONS.save.isSource).toBe(false);
    expect(STEP_DEFINITIONS.save.maxInputs).toBe(1);
  });

  it("join accepts exactly 2 inputs", () => {
    expect(STEP_DEFINITIONS.join.maxInputs).toBe(2);
    expect(STEP_DEFINITIONS.join.minInputs).toBe(2);
    expect(STEP_DEFINITIONS.join.isSource).toBe(false);
    expect(STEP_DEFINITIONS.join.isTerminal).toBe(false);
  });

  it("stream_consume is a source step", () => {
    expect(STEP_DEFINITIONS.stream_consume.isSource).toBe(true);
    expect(STEP_DEFINITIONS.stream_consume.maxInputs).toBe(0);
  });

  it("stream_publish is a terminal step", () => {
    expect(STEP_DEFINITIONS.stream_publish.isTerminal).toBe(true);
  });

  it("all non-source, non-join steps have maxInputs = 1", () => {
    const singleInputSteps = ["filter", "aggregate", "sort", "select",
      "validate", "save", "pivot", "unpivot", "deduplicate", "fill_nulls",
      "rename", "sample", "sql", "wasm_compute", "stream_publish"];
    singleInputSteps.forEach((type) => {
      const def = STEP_DEFINITIONS[type as keyof typeof STEP_DEFINITIONS];
      expect(def.maxInputs).toBe(1);
    });
  });
});
