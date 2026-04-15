import { describe, expect, it } from "vitest";
import type { Connection } from "@xyflow/react";
import { removeEdgeById, validateConnectionCandidate } from "@/hooks/usePipelineEditor";
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
});
