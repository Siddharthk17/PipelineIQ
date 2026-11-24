import { describe, it, expect } from "vitest";
import yaml from "js-yaml";
import { yamlToGraph, graphToYAML } from "@/lib/yamlGraphSync";

// Readversarial-3 regression spec: builder YAML round-trip must preserve
// backend-required params and significant string content verbatim.

type Step = Record<string, unknown>;

function emitSteps(yamlText: string): Step[] {
  const graph = yamlToGraph(yamlText);
  const emitted = graphToYAML(graph);
  const parsed = yaml.load(emitted) as {
    pipeline: { steps: Step[] };
  };
  return parsed.pipeline.steps;
}

function validateRules(yamlText: string): Record<string, unknown>[] {
  const steps = emitSteps(yamlText);
  const validate = steps.find((s) => s.type === "validate");
  expect(validate).toBeDefined();
  return (validate as Step).rules as Record<string, unknown>[];
}

const VALIDATE_YAML = [
  "pipeline:",
  "  name: val_pipe",
  "  steps:",
  "    - name: load_data",
  "      type: load",
  "      file_id: file-1",
  "    - name: check_age",
  "      type: validate",
  "      input: load_data",
  "      rules:",
  "        - check: between",
  "          column: age",
  "          min: 0",
  "          max: 120",
  "        - check: in_values",
  "          column: status",
  '          values: ["active", "pending"]',
  "        - check: matches_pattern",
  "          column: email",
  '          pattern: "^[^@]+@[^@]+$"',
  "        - check: date_format",
  "          column: created",
  '          format: "%Y-%m-%d"',
].join("\n");

describe("F-1 validate rule params survive round-trip", () => {
  it("preserves between min/max", () => {
    const rules = validateRules(VALIDATE_YAML);
    const between = rules.find((r) => r.check === "between");
    expect(between).toMatchObject({ column: "age", min: 0, max: 120 });
  });

  it("preserves in_values values", () => {
    const rules = validateRules(VALIDATE_YAML);
    const inValues = rules.find((r) => r.check === "in_values");
    expect(inValues).toMatchObject({
      column: "status",
      values: ["active", "pending"],
    });
  });

  it("preserves matches_pattern pattern", () => {
    const rules = validateRules(VALIDATE_YAML);
    const pattern = rules.find((r) => r.check === "matches_pattern");
    expect(pattern).toMatchObject({
      column: "email",
      pattern: "^[^@]+@[^@]+$",
    });
  });

  it("preserves date_format format", () => {
    const rules = validateRules(VALIDATE_YAML);
    const dateFormat = rules.find((r) => r.check === "date_format");
    expect(dateFormat).toMatchObject({
      column: "created",
      format: "%Y-%m-%d",
    });
  });
});

describe("F-2 pivot scalar-string index survives round-trip", () => {
  const PIVOT_YAML = [
    "pipeline:",
    "  name: piv_pipe",
    "  steps:",
    "    - name: load_data",
    "      type: load",
    "      file_id: file-1",
    "    - name: piv",
    "      type: pivot",
    "      input: load_data",
    "      index: region",
    "      columns: quarter",
    "      values: sales",
  ].join("\n");

  it("emits scalar index as-is instead of index: []", () => {
    const graph = yamlToGraph(PIVOT_YAML);
    // Ingress keeps the scalar verbatim on the node config.
    const pivNode = graph.nodes.find((n) => n.data.label === "piv");
    expect(pivNode?.data.config.index).toBe("region");
    const steps = emitSteps(PIVOT_YAML);
    const piv = steps.find((s) => s.name === "piv");
    expect(piv?.index).toBe("region");
  });

  it("still emits list index correctly", () => {
    const listYaml = [
      "pipeline:",
      "  name: piv_pipe",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: file-1",
      "    - name: piv",
      "      type: pivot",
      "      input: load_data",
      '      index: ["region", "store"]',
      "      columns: quarter",
      "      values: sales",
    ].join("\n");
    const steps = emitSteps(listYaml);
    const piv = steps.find((s) => s.name === "piv");
    expect(piv?.index).toEqual(["region", "store"]);
  });
});

describe("F-3 string values are not trimmed or quote-stripped", () => {
  it("preserves significant whitespace in filter values", () => {
    const filterYaml = [
      "pipeline:",
      "  name: ws_pipe",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: file-1",
      "    - name: filt",
      "      type: filter",
      "      input: load_data",
      "      column: name",
      "      operator: equals",
      '      value: "  padded  "',
    ].join("\n");
    const steps = emitSteps(filterYaml);
    const filt = steps.find((s) => s.name === "filt");
    expect(filt?.value).toBe("  padded  ");
  });

  it("preserves literal surrounding quotes in values", () => {
    const quotedYaml = [
      "pipeline:",
      "  name: q_pipe",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: '\"file-uuid-123\"'",
    ].join("\n");
    const steps = emitSteps(quotedYaml);
    expect(steps[0]?.file_id).toBe('"file-uuid-123"');
  });

  it("preserves rename mapping values verbatim", () => {
    const renameYaml = [
      "pipeline:",
      "  name: rn_pipe",
      "  steps:",
      "    - name: load_data",
      "      type: load",
      "      file_id: file-1",
      "    - name: ren",
      "      type: rename",
      "      input: load_data",
      "      mapping:",
      '        old_name: "  New Name  "',
    ].join("\n");
    const steps = emitSteps(renameYaml);
    const ren = steps.find((s) => s.name === "ren") as
      | { mapping: Record<string, string> }
      | undefined;
    expect(ren?.mapping).toEqual({ old_name: "  New Name  " });
  });
});
