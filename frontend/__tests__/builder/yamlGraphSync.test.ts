import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { graphToYAML, yamlToGraph, type BuilderEdge, type BuilderNode } from "@/lib/yamlGraphSync";

function makeNode(id: string, type: BuilderNode["data"]["type"], overrides?: Partial<BuilderNode["data"]>): BuilderNode {
  return {
    id,
    type: "stepNode",
    position: { x: 0, y: 0 },
    data: {
      label: id,
      type,
      config: type === "load" ? { file_id: "file_1" } : type === "save" ? { filename: "out" } : {},
      backendSupported: true,
      ...overrides,
    },
  };
}

const SIMPLE_YAML = `
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

const JOIN_YAML = `
pipeline:
  name: join_test
  steps:
    - name: load_orders
      type: load
      file_id: orders
    - name: load_customers
      type: load
      file_id: customers
    - name: join_data
      type: join
      left: load_orders
      right: load_customers
      on: customer_id
      how: inner
    - name: save_result
      type: save
      input: join_data
      filename: joined.csv
`.trim();

describe("yamlToGraph", () => {
  it("returns empty graph for empty string", () => {
    const result = yamlToGraph("");
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
    expect(result.pipelineName).toBe("my_pipeline");
  });

  it("returns empty graph for invalid YAML", () => {
    const result = yamlToGraph("not: valid: yaml: [{{");
    expect(result.nodes).toHaveLength(0);
    expect(result.edges).toHaveLength(0);
  });

  it("creates correct number of nodes", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    expect(nodes).toHaveLength(3);
  });

  it("node ids match step names", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    const ids = nodes.map((n) => n.id).sort();
    expect(ids).toEqual(["delivered_only", "load_sales", "save_output"]);
  });

  it("node type is always stepNode", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    nodes.forEach((n) => expect(n.type).toBe("stepNode"));
  });

  it("step type stored in node data", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    const loadNode = nodes.find((n) => n.id === "load_sales");
    expect(loadNode?.data.type).toBe("load");
  });

  it("step config stored in node data", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    const filterNode = nodes.find((n) => n.id === "delivered_only");
    const config = filterNode?.data.config ?? {};
    expect(config.column).toBe("status");
    expect(config.operator).toBe("equals");
    expect(config.value).toBe("delivered");
  });

  it("creates edges for input connections", () => {
    const { edges } = yamlToGraph(SIMPLE_YAML);
    expect(edges).toHaveLength(2);
  });

  it("edge connects correct source and target", () => {
    const { edges } = yamlToGraph(SIMPLE_YAML);
    const filterEdge = edges.find((e) => e.target === "delivered_only");
    expect(filterEdge?.source).toBe("load_sales");
  });

  it("join step creates left and right edges", () => {
    const { edges } = yamlToGraph(JOIN_YAML);
    const joinEdges = edges.filter((e) => e.target === "join_data");
    expect(joinEdges).toHaveLength(2);
    const leftEdge = joinEdges.find((e) => e.targetHandle === "left");
    const rightEdge = joinEdges.find((e) => e.targetHandle === "right");
    expect(leftEdge?.source).toBe("load_orders");
    expect(rightEdge?.source).toBe("load_customers");
  });

  it("load step has no incoming edges", () => {
    const { edges } = yamlToGraph(SIMPLE_YAML);
    const loadEdges = edges.filter((e) => e.target === "load_sales");
    expect(loadEdges).toHaveLength(0);
  });

  it("all nodes have valid positions", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    nodes.forEach((n) => {
      expect(typeof n.position.x).toBe("number");
      expect(typeof n.position.y).toBe("number");
      expect(Number.isNaN(n.position.x)).toBe(false);
      expect(Number.isNaN(n.position.y)).toBe(false);
    });
  });

  it("source nodes appear at lower x than downstream nodes", () => {
    const { nodes } = yamlToGraph(SIMPLE_YAML);
    const loadNode = nodes.find((n) => n.id === "load_sales")!;
    const filterNode = nodes.find((n) => n.id === "delivered_only")!;
    expect(loadNode.position.x).toBeLessThan(filterNode.position.x);
  });

  it("pipeline name extracted from YAML", () => {
    const graph = yamlToGraph(SIMPLE_YAML);
    expect(graph.pipelineName).toBe("sales_pipeline");
  });
});

describe("graphToYAML", () => {
  it("empty nodes produces empty steps", () => {
    const result = graphToYAML([], [], "test");
    expect(result).toContain("steps: []");
  });

  it("roundtrip preserves step names", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML(nodes, edges, "sales_pipeline");
    expect(result).toContain("name: load_sales");
    expect(result).toContain("name: delivered_only");
    expect(result).toContain("name: save_output");
  });

  it("roundtrip preserves step types", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML(nodes, edges, "sales_pipeline");
    expect(result).toContain("type: load");
    expect(result).toContain("type: filter");
    expect(result).toContain("type: save");
  });

  it("roundtrip preserves input references", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML(nodes, edges, "sales_pipeline");
    expect(result).toContain("input: load_sales");
    expect(result).toContain("input: delivered_only");
  });

  it("roundtrip preserves join left/right", () => {
    const { nodes, edges } = yamlToGraph(JOIN_YAML);
    const result = graphToYAML(nodes, edges, "join_test");
    expect(result).toContain("left: load_orders");
    expect(result).toContain("right: load_customers");
  });

  it("topological order: load appears before filter", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML(nodes, edges, "sales_pipeline");
    const loadPos = result.indexOf("name: load_sales");
    const filterPos = result.indexOf("name: delivered_only");
    expect(loadPos).toBeLessThan(filterPos);
  });

  it("cycle detection returns empty string", () => {
    const nodes = [
      makeNode("a", "filter"),
      makeNode("b", "filter"),
    ];
    const edges: BuilderEdge[] = [
      { id: "a__b", source: "a", target: "b" },
      { id: "b__a", source: "b", target: "a" },
    ];
    const result = graphToYAML(nodes, edges, "cycle_test");
    expect(result).toBe("");
  });

  it("preserves step config values", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML(nodes, edges, "sales_pipeline");
    expect(result).toContain("column: status");
    expect(result).toContain("operator: equals");
  });

  it("accepts BuilderGraph object", () => {
    const { nodes, edges } = yamlToGraph(SIMPLE_YAML);
    const result = graphToYAML({ pipelineName: "test", nodes, edges });
    expect(result).toContain("name: load_sales");
  });

  it("sanitizes embedded quotes from config values", () => {
    const node: BuilderNode = {
      id: "load_data",
      type: "stepNode",
      position: { x: 0, y: 0 },
      data: {
        label: "load_data",
        type: "load",
        config: { file_id: '"file-uuid-123"' },
        backendSupported: true,
      },
    };
    const result = graphToYAML([node], [], "test");
    const parsed = yaml.load(result) as { pipeline: { steps: Array<{ file_id: string }> } };
    expect(parsed.pipeline.steps[0].file_id).toBe("file-uuid-123");
    expect(result).not.toContain('""file-uuid-123""');
  });
});
