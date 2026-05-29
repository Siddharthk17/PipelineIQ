import { describe, expect, it } from "vitest";
import { hasCycle, topologicalSort, wouldCreateCycle } from "@/lib/topologicalSort";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";

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

describe("topologicalSort", () => {
  it("returns empty array for empty nodes", () => {
    expect(topologicalSort([], [])).toEqual([]);
  });

  it("returns single node for single node graph", () => {
    const nodes = [makeNode("load", "load")];
    const result = topologicalSort(nodes, []);
    expect(result).toHaveLength(1);
    expect(result![0].id).toBe("load");
  });

  it("returns nodes in dependency order", () => {
    const nodes = [makeNode("load", "load"), makeNode("filter", "filter"), makeNode("save", "save")];
    const edges: BuilderEdge[] = [
      { id: "e-load-filter", source: "load", target: "filter" },
      { id: "e-filter-save", source: "filter", target: "save" },
    ];

    const sorted = topologicalSort(nodes, edges);
    expect(sorted?.map((node) => node.id)).toEqual(["load", "filter", "save"]);
    expect(hasCycle(nodes, edges)).toBe(false);
  });

  it("places both join parents before join", () => {
    const nodes = [
      makeNode("load_a", "load"),
      makeNode("load_b", "load"),
      makeNode("join_step", "join"),
      makeNode("save_step", "save"),
    ];
    const edges: BuilderEdge[] = [
      { id: "e-a-join", source: "load_a", target: "join_step", targetHandle: "left" },
      { id: "e-b-join", source: "load_b", target: "join_step", targetHandle: "right" },
      { id: "e-join-save", source: "join_step", target: "save_step" },
    ];

    const sorted = topologicalSort(nodes, edges);
    expect(sorted).not.toBeNull();
    const ids = sorted!.map((n) => n.id);
    expect(ids.indexOf("load_a")).toBeLessThan(ids.indexOf("join_step"));
    expect(ids.indexOf("load_b")).toBeLessThan(ids.indexOf("join_step"));
    expect(ids.indexOf("join_step")).toBeLessThan(ids.indexOf("save_step"));
  });

  it("returns null for two-node cycle", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort")];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-a", source: "b", target: "a" },
    ];

    expect(topologicalSort(nodes, edges)).toBeNull();
    expect(hasCycle(nodes, edges)).toBe(true);
  });

  it("returns null for three-node cycle", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort"), makeNode("c", "select")];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-c", source: "b", target: "c" },
      { id: "e-c-a", source: "c", target: "a" },
    ];

    expect(topologicalSort(nodes, edges)).toBeNull();
    expect(hasCycle(nodes, edges)).toBe(true);
  });

  it("handles disconnected nodes", () => {
    const nodes = [makeNode("load", "load"), makeNode("filter", "filter")];
    const result = topologicalSort(nodes, []);
    expect(result).not.toBeNull();
    expect(result).toHaveLength(2);
  });
});

describe("wouldCreateCycle", () => {
  it("returns false for valid new edge", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort")];
    expect(wouldCreateCycle(nodes, [], "a", "b")).toBe(false);
  });

  it("returns true when cycle would be created", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort")];
    const edges: BuilderEdge[] = [{ id: "e-a-b", source: "a", target: "b" }];
    expect(wouldCreateCycle(nodes, edges, "b", "a")).toBe(true);
  });

  it("detects three-node cycle", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort"), makeNode("c", "select")];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-c", source: "b", target: "c" },
    ];
    expect(wouldCreateCycle(nodes, edges, "c", "a")).toBe(true);
  });
});
