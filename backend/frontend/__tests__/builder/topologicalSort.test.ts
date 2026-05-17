import { describe, expect, it } from "vitest";
import { hasCycle, topologicalSort } from "@/lib/topologicalSort";
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

  it("returns null for cyclic graphs", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort")];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-a", source: "b", target: "a" },
    ];

    expect(topologicalSort(nodes, edges)).toBeNull();
    expect(hasCycle(nodes, edges)).toBe(true);
  });
});
