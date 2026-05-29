export type StepCategory = "io" | "transform" | "quality" | "reshape" | "advanced" | "streaming";

export type VisualStepType =
  | "load"
  | "filter"
  | "join"
  | "aggregate"
  | "sort"
  | "select"
  | "transform"
  | "validate"
  | "save"
  | "pivot"
  | "unpivot"
  | "deduplicate"
  | "fill_nulls"
  | "rename"
  | "sample"
  | "sql"
  | "wasm_compute"
  | "stream_consume"
  | "stream_publish";

export interface StepDefinition {
  type: VisualStepType;
  label: string;
  icon: string;
  color: string;
  category: StepCategory;
  maxInputs: number;
  minInputs: number;
  isTerminal: boolean;
  isSource: boolean;
  backendSupported: boolean;
  description: string;
}

export const STEP_DEFINITIONS: Record<VisualStepType, StepDefinition> = {
  load: {
    type: "load",
    label: "Load",
    icon: "\uD83D\uDCC2",
    color: "#3B82F6",
    category: "io",
    maxInputs: 0,
    minInputs: 0,
    isTerminal: false,
    isSource: true,
    backendSupported: true,
    description: "Load a CSV/JSON file",
  },
  filter: {
    type: "filter",
    label: "Filter",
    icon: "\u2B07",
    color: "#10B981",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Filter rows by condition",
  },
  join: {
    type: "join",
    label: "Join",
    icon: "\u27D7",
    color: "#F59E0B",
    category: "transform",
    maxInputs: 2,
    minInputs: 2,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Join two inputs on a key",
  },
  aggregate: {
    type: "aggregate",
    label: "Aggregate",
    icon: "\u2211",
    color: "#EF4444",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Group and aggregate values",
  },
  sort: {
    type: "sort",
    label: "Sort",
    icon: "\u21C5",
    color: "#6366F1",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Sort rows by one column",
  },
  select: {
    type: "select",
    label: "Select",
    icon: "\u2611",
    color: "#8B5CF6",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Keep selected columns",
  },
  transform: {
    type: "transform",
    label: "Transform",
    icon: "\u26A1",
    color: "#A855F7",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: false,
    description: "Expression transform (visual-only placeholder)",
  },
  validate: {
    type: "validate",
    label: "Validate",
    icon: "\u2713",
    color: "#14B8A6",
    category: "quality",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Run data quality checks",
  },
  save: {
    type: "save",
    label: "Save",
    icon: "\uD83D\uDCBE",
    color: "#3B82F6",
    category: "io",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: true,
    isSource: false,
    backendSupported: true,
    description: "Save output file",
  },
  pivot: {
    type: "pivot",
    label: "Pivot",
    icon: "\u2194",
    color: "#F97316",
    category: "reshape",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Long \u2192 wide reshape",
  },
  unpivot: {
    type: "unpivot",
    label: "Unpivot",
    icon: "\u2195",
    color: "#FB923C",
    category: "reshape",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Wide \u2192 long reshape",
  },
  deduplicate: {
    type: "deduplicate",
    label: "Deduplicate",
    icon: "\u2298",
    color: "#84CC16",
    category: "quality",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Drop duplicate rows",
  },
  fill_nulls: {
    type: "fill_nulls",
    label: "Fill Nulls",
    icon: "\u25CB",
    color: "#06B6D4",
    category: "quality",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Fill missing values",
  },
  rename: {
    type: "rename",
    label: "Rename",
    icon: "\u270E",
    color: "#A78BFA",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Rename columns",
  },
  sample: {
    type: "sample",
    label: "Sample",
    icon: "\u2684",
    color: "#F43F5E",
    category: "transform",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Randomly sample rows",
  },
  sql: {
    type: "sql",
    label: "SQL",
    icon: "\u229E",
    color: "#0EA5E9",
    category: "advanced",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Run SQL against upstream data",
  },
  wasm_compute: {
    type: "wasm_compute",
    label: "Wasm UDF",
    icon: "\u29C6",
    color: "#78350F",
    category: "advanced",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: false,
    isSource: false,
    backendSupported: true,
    description: "Execute a custom WebAssembly function per row",
  },
  stream_consume: {
    type: "stream_consume",
    label: "Stream Consume",
    icon: "\u21AF",
    color: "#0EA5E9",
    category: "streaming",
    maxInputs: 0,
    minInputs: 0,
    isTerminal: false,
    isSource: true,
    backendSupported: true,
    description: "Read micro-batches from a Redpanda topic",
  },
  stream_publish: {
    type: "stream_publish",
    label: "Stream Publish",
    icon: "\u21AA",
    color: "#7C3AED",
    category: "streaming",
    maxInputs: 1,
    minInputs: 1,
    isTerminal: true,
    isSource: false,
    backendSupported: true,
    description: "Publish processed rows to a Redpanda topic",
  },
};

export const STEP_CATEGORY_LABELS: Record<StepCategory, string> = {
  io: "I/O",
  transform: "Transform",
  quality: "Quality",
  reshape: "Reshape",
  advanced: "Advanced",
  streaming: "Streaming",
};

export const STEP_TYPES = Object.keys(STEP_DEFINITIONS) as VisualStepType[];

export function isVisualStepType(value: string): value is VisualStepType {
  return value in STEP_DEFINITIONS;
}

export function getDefaultStepConfig(stepType: VisualStepType): Record<string, unknown> {
  switch (stepType) {
    case "load":
      return { file_id: "" };
    case "filter":
      return { column: "", operator: "equals", value: "" };
    case "join":
      return { on: "", how: "inner" };
    case "aggregate":
      return { group_by: [], aggregations: [] };
    case "sort":
      return { by: "", order: "asc" };
    case "select":
      return { columns: [] };
    case "transform":
      return { column: "", expression: "" };
    case "validate":
      return { rules: [] };
    case "save":
      return { filename: "output" };
    case "pivot":
      return { index: [], columns: "", values: "", aggfunc: "sum", fill_value: 0 };
    case "unpivot":
      return { id_vars: [], value_vars: [], var_name: "variable", value_name: "value" };
    case "deduplicate":
      return { subset: null, keep: "first" };
    case "fill_nulls":
      return { strategy: "constant", columns: [], constant_value: "" };
    case "rename":
      return { mapping: {} };
    case "sample":
      return { n: 1000, random_state: 42 };
    case "sql":
      return { query: "SELECT *\nFROM {{input}}\nLIMIT 100" };
    case "wasm_compute":
      return { wasm_file_id: "", function: "", input_columns: [], output_column: "" };
    case "stream_consume":
      return { topic: "", consumer_group: "pipelineiq-analytics", batch_size: 1000, batch_timeout_ms: 5000, deserialize: "json" };
    case "stream_publish":
      return { topic: "", serialize: "json", key_column: null };
  }
}

export const AGGREGATION_FUNCTIONS = [
  { value: "sum", label: "SUM" },
  { value: "count", label: "COUNT" },
  { value: "mean", label: "AVG" },
  { value: "min", label: "MIN" },
  { value: "max", label: "MAX" },
  { value: "first", label: "FIRST" },
  { value: "last", label: "LAST" },
  { value: "count_distinct", label: "COUNT DISTINCT" },
  { value: "percentile_50", label: "MEDIAN (P50)" },
  { value: "percentile_95", label: "P95" },
  { value: "stddev", label: "STDDEV" },
  { value: "variance", label: "VARIANCE" },
] as const;

export const FILTER_OPERATORS = [
  { value: "equals", label: "= equals" },
  { value: "not_equals", label: "\u2260 not equals" },
  { value: "greater_than", label: "> greater than" },
  { value: "less_than", label: "< less than" },
  { value: "gte", label: "\u2265 at least" },
  { value: "lte", label: "\u2264 at most" },
  { value: "contains", label: "\u220B contains" },
  { value: "not_contains", label: "\u220C not contains" },
  { value: "starts_with", label: "\u228F starts with" },
  { value: "ends_with", label: "\u2290 ends with" },
  { value: "is_null", label: "\u2205 is empty" },
  { value: "is_not_null", label: "\u25C9 is not empty" },
] as const;

export const JOIN_TYPES = [
  { value: "inner", label: "INNER JOIN \u2014 matching rows only" },
  { value: "left", label: "LEFT JOIN \u2014 all left rows" },
  { value: "right", label: "RIGHT JOIN \u2014 all right rows" },
  { value: "outer", label: "FULL OUTER \u2014 all rows" },
] as const;

export const FILL_STRATEGIES = [
  { value: "constant", label: "Constant value" },
  { value: "forward_fill", label: "Forward fill (previous)" },
  { value: "backward_fill", label: "Backward fill (next)" },
  { value: "mean", label: "Column mean" },
  { value: "median", label: "Column median" },
  { value: "mode", label: "Most common value" },
] as const;
