import type { Edge, Node } from "@xyflow/react";
import yaml from "js-yaml";
import { STEP_DEFINITIONS, isVisualStepType, type VisualStepType } from "@/lib/stepDefinitions";
import { topologicalSort } from "@/lib/topologicalSort";

export interface StepNodeData extends Record<string, unknown> {
  label: string;
  type: VisualStepType;
  config: Record<string, unknown>;
  backendSupported: boolean;
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

function makeNodeId(index: number, stepName: string): string {
  const suffix = stepName
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/^_+|_+$/g, "");
  return `step_${index}_${suffix || "node"}`;
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
    const columns = asStringArray(config.by);
    config.by = columns[0] ?? "";
  }
  if (
    !config.order &&
    Array.isArray(config.ascending) &&
    typeof config.ascending[0] === "boolean"
  ) {
    config.order = config.ascending[0] ? "asc" : "desc";
  }
  delete config.ascending;
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

  const order = topologicalSort(nodes, edges);
  const incomingByTarget = new Map<string, string[]>();
  for (const edge of edges) {
    const incoming = incomingByTarget.get(edge.target) ?? [];
    incoming.push(edge.source);
    incomingByTarget.set(edge.target, incoming);
  }

  const levelById = new Map<string, number>();
  for (const id of order) {
    const incoming = incomingByTarget.get(id) ?? [];
    if (incoming.length === 0) {
      levelById.set(id, 0);
      continue;
    }
    const level = Math.max(...incoming.map((source) => levelById.get(source) ?? 0)) + 1;
    levelById.set(id, level);
  }

  const rowsPerLevel = new Map<number, number>();
  for (const id of order) {
    const level = levelById.get(id) ?? 0;
    const row = rowsPerLevel.get(level) ?? 0;
    rowsPerLevel.set(level, row + 1);
    positions.set(id, { x: level * 280, y: row * 140 });
  }

  return positions;
}

export function yamlToGraph(yamlText: string): BuilderGraph {
  if (!yamlText.trim()) {
    return { pipelineName: "my_pipeline", nodes: [], edges: [] };
  }

  const rawLoaded = yaml.load(yamlText);
  if (!rawLoaded || typeof rawLoaded !== "object") {
    throw new Error("YAML root must be a mapping object");
  }

  const rawRoot = toRecord(rawLoaded);
  const pipelineRaw = toRecord(rawRoot.pipeline ?? rawRoot);
  const pipelineName = asString(pipelineRaw.name).trim() || "my_pipeline";
  const rawSteps = Array.isArray(pipelineRaw.steps) ? pipelineRaw.steps : [];

  const nodes: BuilderNode[] = [];
  const edges: BuilderEdge[] = [];
  const stepNameToNodeId = new Map<string, string>();
  const rawStepRecords: Record<string, unknown>[] = [];

  rawSteps.forEach((stepValue, index) => {
    const step = toRecord(stepValue);
    rawStepRecords.push(step);

    const rawType = asString(step.type);
    const stepType: VisualStepType = isVisualStepType(rawType) ? rawType : "transform";
    const label = asString(step.name).trim() || `${STEP_DEFINITIONS[stepType].label}_${index + 1}`;
    const id = makeNodeId(index, label);

    nodes.push({
      id,
      type: "stepNode",
      position: { x: 0, y: 0 },
      data: {
        label,
        type: stepType,
        config: extractConfigForUi(stepType, step),
        backendSupported: STEP_DEFINITIONS[stepType].backendSupported,
      },
    });

    if (asString(step.name).trim()) {
      stepNameToNodeId.set(asString(step.name).trim(), id);
    }
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

function normalizeJoinConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  return {
    on: asString(config.on),
    how: asString(config.how) || "inner",
  };
}

function normalizeAggregateConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const groupBy = asStringArray(config.group_by);
  const aggregationsRaw = Array.isArray(config.aggregations) ? config.aggregations : [];
  const aggregations = aggregationsRaw
    .map((agg) => toRecord(agg))
    .filter((agg) => asString(agg.column).trim() && asString(agg.function).trim())
    .map((agg) => ({
      column: asString(agg.column).trim(),
      function: asString(agg.function).trim(),
    }));

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
  const method = asString(config.strategy) || "constant";
  const payload: Record<string, unknown> = {
    method,
    columns: asStringArray(config.columns),
  };
  if (method === "constant") {
    payload.value = config.constant_value ?? "";
  }
  return payload;
}

function normalizeSampleConfigForYaml(config: Record<string, unknown>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};

  if (typeof config.n === "number" && Number.isFinite(config.n)) {
    payload.n = config.n;
  }

  if (typeof config.fraction === "number" && Number.isFinite(config.fraction)) {
    payload.fraction = config.fraction;
  }

  if (typeof config.random_state === "number" && Number.isFinite(config.random_state)) {
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
    .map((rule) => ({
      check: asString(rule.check).trim(),
      column: asString(rule.column).trim(),
      severity: asString(rule.severity).trim() || "warning",
      ...(rule.value !== undefined ? { value: rule.value } : {}),
    }));
  return { rules };
}

function normalizeConfigForYaml(
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
        by: asString(config.by),
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
  }
}

export function graphToYAML(graph: BuilderGraph): string {
  const nodes = graph.nodes ?? [];
  const edges = graph.edges ?? [];
  const order = topologicalSort(nodes, edges);

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

  order.forEach((nodeId, index) => {
    const node = nodeById.get(nodeId);
    if (!node) {
      return;
    }

    const stepType = node.data.type;
    const stepName = sanitizeStepName(node.data.label || `${stepType}_${index + 1}`, index, usedStepNames);
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
      Object.assign(step, normalizeJoinConfigForYaml(node.data.config));
    } else {
      if (STEP_DEFINITIONS[stepType].maxInputs > 0) {
        const sourceEdge = incomingEdges[0];
        step.input = sourceEdge ? stepNameByNodeId.get(sourceEdge.source) ?? "" : "";
      }
      Object.assign(step, normalizeConfigForYaml(stepType, node.data.config));
    }

    steps.push(step);
  });

  return yaml.dump(
    {
      pipeline: {
        name: graph.pipelineName.trim() || "my_pipeline",
        steps,
      },
    },
    {
      noRefs: true,
      lineWidth: 120,
    },
  );
}
