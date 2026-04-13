import { describe, expect, it } from "vitest";
import type { Connection } from "@xyflow/react";
import { validateConnectionCandidate } from "@/hooks/usePipelineEditor";
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
