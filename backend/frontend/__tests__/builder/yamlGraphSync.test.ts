import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { graphToYAML, yamlToGraph, type BuilderEdge, type BuilderNode } from "@/lib/yamlGraphSync";

function makeNode(id: string, type: BuilderNode["data"]["type"]): BuilderNode {
  return {
    id,
    type: "stepNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      type,
      config: type === "load" ? { file_id: "file_1" } : type === "save" ? { filename: "out" } : {},
      backendSupported: true,
    },
  };
}

describe("yamlGraphSync", () => {
  it("round-trips a simple pipeline between YAML and graph", () => {
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
    expect(graph.nodes.map((node) => node.id)).toEqual(["load_sales", "delivered_only", "save_output"]);

    const outputYaml = graphToYAML(graph);
    const parsed = yaml.load(outputYaml) as {
      pipeline: { name: string; steps: Array<Record<string, unknown>> };
    };
    expect(parsed.pipeline.name).toBe("sales_pipeline");
    expect(parsed.pipeline.steps.map((step) => step.type)).toEqual(["load", "filter", "save"]);
  });

  it("returns an empty graph for invalid YAML", () => {
    const graph = yamlToGraph("pipeline: [");
    expect(graph).toEqual({
      pipelineName: "my_pipeline",
      nodes: [],
      edges: [],
    });
  });

  it("returns empty string when graph contains a cycle", () => {
    const nodes = [makeNode("a", "filter"), makeNode("b", "sort")];
    const edges: BuilderEdge[] = [
      { id: "e-a-b", source: "a", target: "b" },
      { id: "e-b-a", source: "b", target: "a" },
    ];

    expect(graphToYAML({ pipelineName: "cyclic", nodes, edges })).toBe("");
  });
});
