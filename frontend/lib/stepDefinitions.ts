export type StepCategory = "io" | "transform" | "quality" | "reshape" | "advanced";

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
  | "sql";

export interface StepDefinition {
  type: VisualStepType;
  label: string;
  icon: string;
  color: string;
  category: StepCategory;
  maxInputs: number;
  backendSupported: boolean;
  description: string;
}

export const STEP_DEFINITIONS: Record<VisualStepType, StepDefinition> = {
  load: {
    type: "load",
    label: "Load",
    icon: "📥",
    color: "#3B82F6",
    category: "io",
    maxInputs: 0,
    backendSupported: true,
    description: "Load a CSV/JSON file",
  },
  filter: {
    type: "filter",
    label: "Filter",
    icon: "🔎",
    color: "#10B981",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Filter rows by condition",
  },
  join: {
    type: "join",
    label: "Join",
    icon: "🔗",
    color: "#F59E0B",
    category: "transform",
    maxInputs: 2,
    backendSupported: true,
    description: "Join two inputs on a key",
  },
  aggregate: {
    type: "aggregate",
    label: "Aggregate",
    icon: "Σ",
    color: "#EF4444",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Group and aggregate values",
  },
  sort: {
    type: "sort",
    label: "Sort",
    icon: "↕",
    color: "#6366F1",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Sort rows by one column",
  },
  select: {
    type: "select",
    label: "Select",
    icon: "☑",
    color: "#8B5CF6",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Keep selected columns",
  },
  transform: {
    type: "transform",
    label: "Transform",
    icon: "fx",
    color: "#A855F7",
    category: "transform",
    maxInputs: 1,
    backendSupported: false,
    description: "Expression transform (visual-only placeholder)",
  },
  validate: {
    type: "validate",
    label: "Validate",
    icon: "✓",
    color: "#14B8A6",
    category: "quality",
    maxInputs: 1,
    backendSupported: true,
    description: "Run data quality checks",
  },
  save: {
    type: "save",
    label: "Save",
    icon: "💾",
    color: "#3B82F6",
    category: "io",
    maxInputs: 1,
    backendSupported: true,
    description: "Save output file",
  },
  pivot: {
    type: "pivot",
    label: "Pivot",
    icon: "↔",
    color: "#F97316",
    category: "reshape",
    maxInputs: 1,
    backendSupported: true,
    description: "Long → wide reshape",
  },
  unpivot: {
    type: "unpivot",
    label: "Unpivot",
    icon: "↕",
    color: "#FB923C",
    category: "reshape",
    maxInputs: 1,
    backendSupported: true,
    description: "Wide → long reshape",
  },
  deduplicate: {
    type: "deduplicate",
    label: "Deduplicate",
    icon: "🧹",
    color: "#84CC16",
    category: "quality",
    maxInputs: 1,
    backendSupported: true,
    description: "Drop duplicate rows",
  },
  fill_nulls: {
    type: "fill_nulls",
    label: "Fill Nulls",
    icon: "∅",
    color: "#06B6D4",
    category: "quality",
    maxInputs: 1,
    backendSupported: true,
    description: "Fill missing values",
  },
  rename: {
    type: "rename",
    label: "Rename",
    icon: "✎",
    color: "#A78BFA",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Rename columns",
  },
  sample: {
    type: "sample",
    label: "Sample",
    icon: "🎲",
    color: "#F43F5E",
    category: "transform",
    maxInputs: 1,
    backendSupported: true,
    description: "Randomly sample rows",
  },
  sql: {
    type: "sql",
    label: "SQL",
    icon: "🧮",
    color: "#0EA5E9",
    category: "advanced",
    maxInputs: 1,
    backendSupported: true,
    description: "Run SQL against upstream data",
  },
};

export const STEP_CATEGORY_LABELS: Record<StepCategory, string> = {
  io: "I/O",
  transform: "Transform",
  quality: "Quality",
  reshape: "Reshape",
  advanced: "Advanced",
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
  }
}
