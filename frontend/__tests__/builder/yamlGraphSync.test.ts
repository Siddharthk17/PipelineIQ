import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { graphToYAML, yamlToGraph, type BuilderEdge, type BuilderNode } from "@/lib/yamlGraphSync";

// Import internal functions for unit testing via dynamic access pattern
// (sanitizeYamlValue and sanitizeConfig are exported via graphToYAML's behavior)

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

  it("sanitizes embedded quotes from node config values before YAML dump", () => {
    const node: BuilderNode = {
      id: "load_data",
      type: "stepNode",
      position: { x: 0, y: 0 },
      data: {
        label: "load_data",
        type: "load",
        // Simulates a value that acquired surrounding quotes from a js-yaml round-trip
        config: { file_id: '"file-uuid-123"' },
        backendSupported: true,
      },
    };
    const edges: BuilderEdge[] = [];

    const outputYaml = graphToYAML({ pipelineName: "test", nodes: [node], edges });
    const parsed = yaml.load(outputYaml) as {
      pipeline: { steps: Array<{ file_id: string }> };
    };

    expect(parsed.pipeline.steps[0].file_id).toBe("file-uuid-123");
    // The YAML text must not contain doubled quotes
    expect(outputYaml).not.toContain('""file-uuid-123""');
    expect(outputYaml).not.toContain("'\"file-uuid-123\"'");
  });

  it("leaves plain file_id values untouched", () => {
    const node: BuilderNode = {
      id: "load_data",
      type: "stepNode",
      position: { x: 0, y: 0 },
      data: {
        label: "load_data",
        type: "load",
        config: { file_id: "abc-123-def" },
        backendSupported: true,
      },
    };
    const edges: BuilderEdge[] = [];

    const outputYaml = graphToYAML({ pipelineName: "test", nodes: [node], edges });
    const parsed = yaml.load(outputYaml) as {
      pipeline: { steps: Array<{ file_id: string }> };
    };

    expect(parsed.pipeline.steps[0].file_id).toBe("abc-123-def");
  });
});
