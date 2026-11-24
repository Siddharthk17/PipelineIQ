import type { Edge, Node } from "@xyflow/react";
import yaml from "js-yaml";
import { STEP_DEFINITIONS, isVisualStepType, type VisualStepType } from "@/lib/stepDefinitions";
import { topologicalSort } from "@/lib/topologicalSort";

export interface StepNodeData extends Record<string, unknown> {
  label: string;
  type: VisualStepType;
  config: Record<string, unknown>;
  backendSupported: boolean;
  inferredSchema?: string[];
  outputSchema?: string[];
  validationError?: string;
  onConfigure?: (nodeId: string) => void;
  onDelete?: (nodeId: string) => void;
}

export type BuilderNode = Node<StepNodeData>;
export type BuilderEdge = Edge;

export interface BuilderGraph {
  pipelineName: string;
  nodes: BuilderNode[];
  edges: BuilderEdge[];
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((item): item is string => typeof item === "string");
}

function toEdgeId(source: string, target: string, targetHandle?: string): string {
  return `e-${source}-${target}-${targetHandle ?? "input"}`;
}

function makeNodeId(stepName: string, index: number, usedIds: Set<string>): string {
  const normalized = stepName
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");

  const base = normalized || `step_${index + 1}`;
  let candidate = base;
  let suffix = 1;
  while (usedIds.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  usedIds.add(candidate);
  return candidate;
}

function sanitizeStepName(rawName: string, index: number, usedNames: Set<string>): string {
  const normalized = rawName
    .trim()
    .replace(/[^a-zA-Z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");

  let base = normalized || `step_${index + 1}`;
  if (/^[0-9]/.test(base)) {
    base = `step_${base}`;
  }

  let candidate = base;
  let suffix = 1;
  while (usedNames.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  usedNames.add(candidate);
  return candidate;
}

function normalizeFillNullsConfigForUi(step: Record<string, unknown>): Record<string, unknown> {
  const config: Record<string, unknown> = { ...step };
  delete config.name;
  delete config.type;
  delete config.input;
  delete config.left;
  delete config.right;

  if (typeof config.method === "string") {
    config.strategy = config.method;
  }
  if (config.value !== undefined && config.constant_value === undefined) {
    config.constant_value = config.value;
  }
  delete config.method;
  delete config.value;
  return config;
}

function normalizeSortConfigForUi(step: Record<string, unknown>): Record<string, unknown> {
  const config: Record<string, unknown> = { ...step };
  delete config.name;
  delete config.type;
  delete config.input;
  delete config.left;
  delete config.right;

  if (Array.isArray(config.by)) {
    // M-046: keep every key verbatim (backend accepts list `by` for
    // multi-key sorts). The config panel only edits single-column sorts.
    config.by = asStringArray(config.by);
  }
  if (
    !config.order &&
    Array.isArray(config.ascending) &&
    typeof config.ascending[0] === "boolean"
  ) {
    config.order = config.ascending[0] ? "asc" : "desc";
  }
  // M-046: keep `ascending` — the backend prefers it over `order`
  // (steps/_core.py, sql_builder.py), so deleting it dropped per-key
  // directions on round-trip.
  return config;
}

function normalizeSampleConfigForUi(step: Record<string, unknown>): Record<string, unknown> {
  const config: Record<string, unknown> = { ...step };
  delete config.name;
  delete config.type;
  delete config.input;
  delete config.left;
  delete config.right;

  if (config.frac !== undefined && config.fraction === undefined) {
    config.fraction = config.frac;
  }
  delete config.frac;
  return config;
}

function extractConfigForUi(
  stepType: VisualStepType,
  step: Record<string, unknown>,
): Record<string, unknown> {
  if (stepType === "fill_nulls") {
    return normalizeFillNullsConfigForUi(step);
  }
  if (stepType === "sort") {
    return normalizeSortConfigForUi(step);
  }
  if (stepType === "sample") {
    return normalizeSampleConfigForUi(step);
  }

  const config: Record<string, unknown> = { ...step };
  delete config.name;
  delete config.type;
  delete config.input;
  delete config.left;
  delete config.right;
  return config;
}

function computeNodePositions(nodes: BuilderNode[], edges: BuilderEdge[]): Map<string, { x: number; y: number }> {
  const positions = new Map<string, { x: number; y: number }>();
  if (nodes.length === 0) {
    return positions;
  }

  const orderedNodes = topologicalSort(nodes, edges) ?? [...nodes].sort((a, b) => a.id.localeCompare(b.id));
  const incomingByTarget = new Map<string, string[]>();
  for (const edge of edges) {
    const incoming = incomingByTarget.get(edge.target) ?? [];
    incoming.push(edge.source);
    incomingByTarget.set(edge.target, incoming);
  }

  const levelById = new Map<string, number>();
  for (const node of orderedNodes) {
    const id = node.id;
    const incoming = incomingByTarget.get(id) ?? [];
    if (incoming.length === 0) {
      levelById.set(id, 0);
      continue;
    }
    const level = Math.max(...incoming.map((source) => levelById.get(source) ?? 0)) + 1;
    levelById.set(id, level);
  }

  const rowsPerLevel = new Map<number, number>();
  for (const node of orderedNodes) {
    const id = node.id;
    const level = levelById.get(id) ?? 0;
    const row = rowsPerLevel.get(level) ?? 0;
    rowsPerLevel.set(level, row + 1);
    positions.set(id, { x: level * 280, y: row * 140 });
  }

  return positions;
}

export interface YamlParseDiagnostics {
  graph: BuilderGraph;
  /** Null when parsing succeeded; human-readable reason otherwise. */
  error: string | null;
}

export function parseYamlWithDiagnostics(yamlText: string): YamlParseDiagnostics {
  const emptyGraph: BuilderGraph = { pipelineName: "my_pipeline", nodes: [], edges: [] };
  if (!yamlText.trim()) {
    return { graph: emptyGraph, error: null };
  }

  let rawLoaded: unknown;
  try {
    // json:true tolerates duplicated mapping keys (last wins, like
    // JSON.parse) instead of throwing. Collaborative editing can otherwise
    // leave merged duplicates in the doc, which used to fail the whole
    // parse and silently empty the canvas.
    rawLoaded = yaml.load(yamlText, { json: true });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Failed to parse YAML";
    return { graph: emptyGraph, error: message };
  }
  if (!rawLoaded || typeof rawLoaded !== "object" || Array.isArray(rawLoaded)) {
    return { graph: emptyGraph, error: "Pipeline YAML must be a mapping with steps." };
  }

  const rawRoot = toRecord(rawLoaded);
  const pipelineRaw = toRecord(rawRoot.pipeline ?? rawRoot);
  const pipelineName = asString(pipelineRaw.name).trim() || "my_pipeline";
  const rawSteps = Array.isArray(pipelineRaw.steps) ? pipelineRaw.steps : [];
  if (rawSteps.length === 0) {
    return {
      graph: { pipelineName, nodes: [], edges: [] },
      error: null,
    };
  }

  const graph = buildGraphFromSteps(pipelineName, rawSteps);
  return { graph, error: null };
}

export function yamlToGraph(yamlText: string): BuilderGraph {
  return parseYamlWithDiagnostics(yamlText).graph;
}

function buildGraphFromSteps(pipelineName: string, rawSteps: unknown[]): BuilderGraph {
  const nodes: BuilderNode[] = [];
  const edges: BuilderEdge[] = [];
  const stepNameToNodeId = new Map<string, string>();
  const usedNodeIds = new Set<string>();
  const rawStepRecords: Record<string, unknown>[] = [];

  rawSteps.forEach((stepValue, index) => {
    const step = toRecord(stepValue);
    rawStepRecords.push(step);

    const rawType = asString(step.type);
    // M-046: never coerce an unrecognized backend type to "transform" —
    // keep it verbatim so the emit path can pass it (and its params)
    // through untouched. Renderers handle the missing definition
    // (StepNode shows an "Unknown type" placeholder; the config panel stays
    // closed; connection validation fail-closes).
    const stepType = rawType as VisualStepType;
    const definition = isVisualStepType(rawType) ? STEP_DEFINITIONS[rawType] : undefined;
    const stepName = asString(step.name).trim() || `${definition?.label || rawType || "step"}_${index + 1}`;
    const id = makeNodeId(stepName, index, usedNodeIds);

    const config = extractConfigForUi(stepType, step);
    if (!definition) {
      // Unmodeled type: graphToYAML cannot infer input wiring from a step
      // definition, so keep the original input/left/right refs in the config
      // for verbatim re-emit. (Users cannot rewire these nodes — connection
      // validation rejects unknown types — so a deleted edge can leave a
      // stale ref here; backend validation reports dangling refs.)
      for (const key of ["input", "left", "right"] as const) {
        if (step[key] !== undefined) {
          config[key] = step[key];
        }
      }
    }

    nodes.push({
      id,
      type: "stepNode",
      position: { x: 0, y: 0 },
      data: {
        label: stepName,
        type: stepType,
        config,
        backendSupported: definition?.backendSupported ?? false,
      },
    });

    stepNameToNodeId.set(stepName, id);
  });

  rawStepRecords.forEach((step, index) => {
    const targetId = nodes[index]?.id;
    if (!targetId) {
      return;
    }

    const stepType = nodes[index]?.data.type;
    if (!stepType) {
      return;
    }

    if (stepType === "join") {
      const leftRef = asString(step.left).trim();
      const rightRef = asString(step.right).trim();

      const leftSourceId = stepNameToNodeId.get(leftRef);
      if (leftSourceId) {
        edges.push({
          id: toEdgeId(leftSourceId, targetId, "left"),
          source: leftSourceId,
          sourceHandle: "output",
          target: targetId,
          targetHandle: "left",
          animated: true,
        });
      }

      const rightSourceId = stepNameToNodeId.get(rightRef);
      if (rightSourceId) {
        edges.push({
          id: toEdgeId(rightSourceId, targetId, "right"),
          source: rightSourceId,
          sourceHandle: "output",
          target: targetId,
          targetHandle: "right",
          animated: true,
        });
      }
      return;
    }

    const inputRef = asString(step.input).trim();
    const sourceId = stepNameToNodeId.get(inputRef);
    if (sourceId) {
      edges.push({
        id: toEdgeId(sourceId, targetId, "input"),
        source: sourceId,
        sourceHandle: "output",
        target: targetId,
        targetHandle: "input",
        animated: false,
      });
    }
  });

  const positions = computeNodePositions(nodes, edges);
  const positionedNodes = nodes.map((node) => ({
    ...node,
    position: positions.get(node.id) ?? node.position,
  }));

  return {
    pipelineName,
    nodes: positionedNodes,
    edges,
  };
}

function sanitizeYamlValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  let cleaned = value.trim();
  const first = cleaned[0];
  const last = cleaned[cleaned.length - 1];
  if ((first === '"' || first === "'") && first === last) {
    const inner = cleaned.slice(1, -1);
    if (inner.length > 0 && !inner.includes(first)) {
      cleaned = inner;
    }
  }
  return cleaned;
}

function sanitizeConfig(
  config: Record<string, unknown>
): Record<string, unknown> {
  const cleaned: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (typeof value === "string") {
      cleaned[key] = sanitizeYamlValue(value);
    } else if (Array.isArray(value)) {
      cleaned[key] = value.map((item) =>
        typeof item === "string" ? sanitizeYamlValue(item) : item
      );
    } else if (value && typeof value === "object") {
      cleaned[key] = sanitizeConfig(
        value as Record<string, unknown>
      );
    } else {
      cleaned[key] = value;
    }
  }
  return cleaned;
}

function normalizeJoinConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  return {
    on: asString(config.on),
    how: asString(config.how) || "inner",
  };
}

function normalizeAggregateConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const groupBy = asStringArray(config.group_by);
  const rawAggregations = config.aggregations;
  // M-046: the backend accepts dict-form aggregations ({column: function})
  // as well as the list form (parser.py). Normalize both to the list form.
  const aggregationList = Array.isArray(rawAggregations)
    ? rawAggregations
    : rawAggregations && typeof rawAggregations === "object"
      ? Object.entries(rawAggregations).map(([column, fn]) => ({ column, function: fn }))
      : [];
  const aggregations = aggregationList
    .map((agg) => toRecord(agg))
    .filter((agg) => asString(agg.column).trim() && asString(agg.function).trim())
    .map((agg) => {
      const normalized: Record<string, unknown> = {
        column: asString(agg.column).trim(),
        function: asString(agg.function).trim(),
      };
      // M-046: keep backend-supported per-aggregation keys (e.g. `alias`,
      // used for output column naming) instead of dropping them.
      for (const [key, value] of Object.entries(agg)) {
        if (key !== "column" && key !== "function" && !(key in normalized)) {
          normalized[key] = value;
        }
      }
      return normalized;
    });

  return { group_by: groupBy, aggregations };
}

function normalizePivotConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  return {
    index: asStringArray(config.index),
    columns: asString(config.columns),
    values: asString(config.values),
    aggfunc: asString(config.aggfunc) || "sum",
    fill_value: config.fill_value ?? 0,
  };
}

function normalizeUnpivotConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  return {
    id_vars: asStringArray(config.id_vars),
    value_vars: asStringArray(config.value_vars),
    var_name: asString(config.var_name) || "variable",
    value_name: asString(config.value_name) || "value",
  };
}

function normalizeFillNullsConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  // M-046: emit the backend-canonical keys (`strategy`/`constant_value`,
  // parser.py + steps/_core.py). The old `method`/`value` names are only
  // tolerated on the DuckDB path and fail parser validation.
  const strategy = asString(config.strategy) || "constant";
  const payload: Record<string, unknown> = {
    strategy,
    columns: asStringArray(config.columns),
  };
  if (strategy === "constant") {
    payload.constant_value = config.constant_value ?? "";
  }
  return payload;
}

function normalizeSampleConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};

  // M-046: pass values through verbatim instead of dropping non-numeric
  // ones — backend validation (not the emit path) reports bad types.
  const hasFraction = config.fraction !== undefined && config.fraction !== null;
  const hasN = config.n !== undefined && config.n !== null;

  if (hasN && !hasFraction) {
    payload.n = config.n;
  }

  if (hasFraction) {
    payload.fraction = config.fraction;
  }

  if (config.random_state !== undefined) {
    payload.random_state = config.random_state;
  } else {
    payload.random_state = 42;
  }

  const stratifyBy = asString(config.stratify_by).trim();
  if (stratifyBy) {
    payload.stratify_by = stratifyBy;
  }

  return payload;
}

function normalizeRenameConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const mapping = toRecord(config.mapping);
  const cleaned = Object.fromEntries(
    Object.entries(mapping)
      .filter(([key, value]) => key.trim() && typeof value === "string" && value.trim())
      .map(([key, value]) => [key, typeof value === "string" ? value.trim() : ""]),
  );
  return { mapping: cleaned };
}

function normalizeValidateConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const rulesRaw = Array.isArray(config.rules) ? config.rules : [];
  const rules = rulesRaw
    .map((rule) => toRecord(rule))
    .filter((rule) => asString(rule.check).trim().length > 0)
    .map((rule) => {
      const normalized: Record<string, unknown> = {
        check: asString(rule.check).trim(),
        column: asString(rule.column).trim(),
        severity: asString(rule.severity).trim() || "warning",
        ...(rule.value !== undefined ? { value: rule.value } : {}),
      };
      // F-1: preserve every other per-rule param verbatim (min/max for
      // between, values for in_values, pattern for matches_pattern, format
      // for date_format, ...). The top-level passthrough in
      // normalizeConfigForYaml cannot see nested rule keys, so the emit
      // whitelist must not drop keys it does not model — backend
      // validation (parser.py _VALIDATE_RULE_PARAMS) requires them.
      for (const [key, value] of Object.entries(rule)) {
        if (!(key in normalized)) {
          normalized[key] = value;
        }
      }
      return normalized;
    });
  return { rules };
}

function normalizeKnownConfigForYaml(
  stepType: VisualStepType,
  config: Record<string, unknown>,
): Record<string, unknown> {
  switch (stepType) {
    case "load":
      return { file_id: asString(config.file_id) };
    case "filter":
      return {
        column: asString(config.column),
        operator: asString(config.operator) || "equals",
        value: config.value ?? "",
      };
    case "join":
      return normalizeJoinConfigForYaml(config);
    case "aggregate":
      return normalizeAggregateConfigForYaml(config);
    case "sort":
      return {
        // M-046: `by` may be a multi-key list; keep its shape verbatim.
        by: Array.isArray(config.by) ? asStringArray(config.by) : asString(config.by),
        order: asString(config.order) || "asc",
      };
    case "select":
      return { columns: asStringArray(config.columns) };
    case "transform":
      return {
        column: asString(config.column),
        expression: asString(config.expression),
      };
    case "validate":
      return normalizeValidateConfigForYaml(config);
    case "save":
      return { filename: asString(config.filename) || "output" };
    case "pivot":
      return normalizePivotConfigForYaml(config);
    case "unpivot":
      return normalizeUnpivotConfigForYaml(config);
    case "deduplicate":
      return {
        subset: Array.isArray(config.subset) ? asStringArray(config.subset) : null,
        keep: asString(config.keep) || "first",
      };
    case "fill_nulls":
      return normalizeFillNullsConfigForYaml(config);
    case "rename":
      return normalizeRenameConfigForYaml(config);
    case "sample":
      return normalizeSampleConfigForYaml(config);
    case "sql":
      return {
        query: asString(config.query) || "SELECT *\nFROM {{input}}\nLIMIT 100",
      };
    case "wasm_compute":
      return {
        wasm_file_id: asString(config.wasm_file_id),
        function: asString(config.function),
        input_columns: asStringArray(config.input_columns),
        output_column: asString(config.output_column) || "result",
      };
    case "stream_consume":
      return {
        topic: asString(config.topic),
        consumer_group: asString(config.consumer_group) || "pipelineiq-analytics",
        batch_size: typeof config.batch_size === "number" ? config.batch_size : 1000,
        batch_timeout_ms: typeof config.batch_timeout_ms === "number" ? config.batch_timeout_ms : 5000,
        deserialize: asString(config.deserialize) || "json",
      };
    case "stream_publish":
      return {
        topic: asString(config.topic),
        serialize: asString(config.serialize) || "json",
        key_column: config.key_column ? asString(config.key_column) : null,
      };
    default:
      // M-046: unmodeled (unknown-to-the-builder) step type — emit nothing
      // here; the passthrough wrapper below re-emits the stored config keys.
      return {};
  }
}

function normalizeConfigForYaml(
  stepType: VisualStepType,
  config: Record<string, unknown>,
): Record<string, unknown> {
  const base = normalizeKnownConfigForYaml(stepType, config);
  // M-046: keep backend-supported keys the whitelist above does not model
  // (alias, contract, ascending, future keys) verbatim instead of dropping
  // them on round-trip.
  const merged = { ...base };
  for (const [key, value] of Object.entries(config)) {
    if (!(key in merged)) {
      merged[key] = value;
    }
  }
  return merged;
}

export function graphToYAML(nodesOrGraph: BuilderNode[] | BuilderGraph, edgesParam?: BuilderEdge[], pipelineNameParam?: string): string {
  let nodes: BuilderNode[];
  let edges: BuilderEdge[];
  let pipelineName: string;

  if (Array.isArray(nodesOrGraph)) {
    nodes = nodesOrGraph;
    edges = edgesParam ?? [];
    pipelineName = pipelineNameParam ?? "my_pipeline";
  } else {
    nodes = nodesOrGraph.nodes ?? [];
    edges = nodesOrGraph.edges ?? [];
    pipelineName = nodesOrGraph.pipelineName ?? "my_pipeline";
  }

  if (nodes.length === 0) {
    return `pipeline:\n  name: ${pipelineName}\n  steps: []\n`;
  }

  const orderedNodes = topologicalSort(nodes, edges);
  if (!orderedNodes) {
    // M-119: a cyclic graph must never serialize to "" — the emit path
    // would overwrite the user's YAML with an empty document. Throw so
    // the caller surfaces a parse error and keeps the YAML intact.
    throw new Error("Graph contains a cycle; resolve it before emitting YAML");
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const incomingByTarget = new Map<string, BuilderEdge[]>();
  for (const edge of edges) {
    const incoming = incomingByTarget.get(edge.target) ?? [];
    incoming.push(edge);
    incomingByTarget.set(edge.target, incoming);
  }

  const usedStepNames = new Set<string>();
  const stepNameByNodeId = new Map<string, string>();
  const steps: Record<string, unknown>[] = [];

  orderedNodes.forEach((node, index) => {
    const nodeId = node.id;
    const resolvedNode = nodeById.get(nodeId) ?? node;
    const stepType = resolvedNode.data.type;
    const stepName = sanitizeStepName(
      resolvedNode.data.label || `${stepType}_${index + 1}`,
      index,
      usedStepNames,
    );
    stepNameByNodeId.set(nodeId, stepName);

    const step: Record<string, unknown> = {
      name: stepName,
      type: stepType,
    };

    const incomingEdges = (incomingByTarget.get(nodeId) ?? []).filter((edge) =>
      stepNameByNodeId.has(edge.source),
    );

    if (stepType === "join") {
      const leftEdge = incomingEdges.find((edge) => edge.targetHandle === "left") ?? incomingEdges[0];
      const rightEdge =
        incomingEdges.find((edge) => edge.targetHandle === "right") ??
        incomingEdges.find((edge) => edge !== leftEdge);

      step.left = leftEdge ? stepNameByNodeId.get(leftEdge.source) ?? "" : "";
      step.right = rightEdge ? stepNameByNodeId.get(rightEdge.source) ?? "" : "";
      // M-046: same whitelist + verbatim-passthrough path as other types, so
      // extras (e.g. a per-step contract on a join) survive round-trip.
      Object.assign(step, sanitizeConfig(normalizeConfigForYaml("join", resolvedNode.data.config)));
    } else {
      const definition = STEP_DEFINITIONS[stepType];
      if (definition && definition.maxInputs > 0) {
        const sourceEdge = incomingEdges[0];
        step.input = sourceEdge ? stepNameByNodeId.get(sourceEdge.source) ?? "" : "";
      }
      Object.assign(step, sanitizeConfig(normalizeConfigForYaml(stepType, resolvedNode.data.config)));
    }

    steps.push(step);
  });

  return yaml.dump(
    {
      pipeline: {
        name: pipelineName.trim() || "my_pipeline",
        steps,
      },
    },
    {
      noRefs: true,
      lineWidth: 120,
    },
  );
}
