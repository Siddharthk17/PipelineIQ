import yaml from "js-yaml";
import { describe, expect, it } from "vitest";
import { graphToYAML, yamlToGraph } from "@/lib/yamlGraphSync";

// M-046: the visual builder dropped backend-supported constructs on YAML
// round-trip (graph -> YAML emit runs on any node/edge change). Each case
// below failed before the fix and passes after it.
const PRESERVATION_YAML = `
pipeline:
  name: preservation_pipe
  steps:
    - name: raw
      type: load
      file_id: file_1
      alias: bronze_extract
    - name: checked
      type: filter
      input: raw
      column: status
      operator: equals
      value: delivered
      contract:
        columns:
          - name: status
            dtype: string
            required: true
    - name: rolled_up
      type: aggregate
      input: checked
      group_by: [region]
      aggregations:
        amount: sum
        order_id: count
    - name: ordered
      type: sort
      input: rolled_up
      by: [region, amount]
      ascending: [true, false]
    - name: future_step
      type: some_future_step
      input: ordered
      custom_param: hello
    - name: filled
      type: fill_nulls
      input: ordered
      strategy: constant
      columns: [amount]
      constant_value: 0
    - name: sampled
      type: sample
      input: filled
      n: 500
      random_state: seed-7
      stratify_by: region
    - name: out
      type: save
      input: sampled
      filename: out
`.trim();

function roundTripSteps() {
  const graph = yamlToGraph(PRESERVATION_YAML);
  const emitted = graphToYAML(graph);
  const parsed = yaml.load(emitted) as {
    pipeline: { steps: Array<Record<string, unknown>> };
  };
  const byName = new Map(parsed.pipeline.steps.map((step) => [step.name, step]));
  return { graph, emitted, byName };
}

describe("M-046 yaml round-trip preservation", () => {
  it("preserves alias on load steps", () => {
    const { byName } = roundTripSteps();
    expect(byName.get("raw")?.alias).toBe("bronze_extract");
  });

  it("preserves per-step contract", () => {
    const { byName } = roundTripSteps();
    expect(byName.get("checked")?.contract).toEqual({
      columns: [{ name: "status", dtype: "string", required: true }],
    });
  });

  it("preserves dict-form aggregations", () => {
    const { byName } = roundTripSteps();
    const aggregations = byName.get("rolled_up")?.aggregations as unknown;
    const pairs = Array.isArray(aggregations)
      ? (aggregations as Array<Record<string, string>>).map((agg) => [agg.column, agg.function])
      : Object.entries((aggregations ?? {}) as Record<string, string>);
    expect(pairs).toContainEqual(["amount", "sum"]);
    expect(pairs).toContainEqual(["order_id", "count"]);
  });

  it("preserves multi-key sort by + ascending array", () => {
    const { byName } = roundTripSteps();
    expect(byName.get("ordered")?.by).toEqual(["region", "amount"]);
    expect(byName.get("ordered")?.ascending).toEqual([true, false]);
  });

  it("does not re-type unknown step types (verbatim passthrough)", () => {
    const { graph, byName } = roundTripSteps();
    const node = graph.nodes.find((candidate) => candidate.id === "future_step");
    expect(String(node?.data.type)).toBe("some_future_step");
    expect(byName.get("future_step")?.type).toBe("some_future_step");
    expect(byName.get("future_step")?.custom_param).toBe("hello");
    expect(byName.get("future_step")?.input).toBe("ordered");
  });

  it("emits fill_nulls with backend-canonical strategy/constant_value keys", () => {
    const { byName } = roundTripSteps();
    const filled = byName.get("filled") ?? {};
    expect(filled.strategy).toBe("constant");
    expect(filled.constant_value).toBe(0);
    expect(filled).not.toHaveProperty("method");
    expect(filled).not.toHaveProperty("value");
  });

  it("passes sample n/random_state through without coercing types", () => {
    const { byName } = roundTripSteps();
    const sampled = byName.get("sampled") ?? {};
    expect(sampled.n).toBe(500);
    // Non-numeric random_state used to be replaced with the default 42;
    // verbatim passthrough lets backend validation report it instead.
    expect(sampled.random_state).toBe("seed-7");
    expect(sampled.stratify_by).toBe("region");
  });
});
