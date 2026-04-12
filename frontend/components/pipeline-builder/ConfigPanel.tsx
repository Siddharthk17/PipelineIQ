import { useMemo, useState } from "react";
import type { UploadedFile } from "@/lib/types";
import { STEP_DEFINITIONS } from "@/lib/stepDefinitions";
import type { BuilderNode } from "@/lib/yamlGraphSync";

interface ConfigPanelProps {
  node: BuilderNode | null;
  availableFiles: UploadedFile[];
  availableColumns: string[];
  onSave: (nodeId: string, config: Record<string, unknown>) => void;
  onClose: () => void;
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

function parseCsv(text: string): string[] {
  return text
    .split(",")
    .map((token) => token.trim())
    .filter(Boolean);
}

function parseMapping(lines: string): Record<string, string> {
  const mapping: Record<string, string> = {};
  for (const rawLine of lines.split("\n")) {
    const line = rawLine.trim();
    if (!line) {
      continue;
    }
    const [from, to] = line.split(":").map((part) => part.trim());
    if (from && to) {
      mapping[from] = to;
    }
  }
  return mapping;
}

function mappingToText(mapping: Record<string, unknown>): string {
  return Object.entries(mapping)
    .map(([from, to]) => `${from}:${String(to)}`)
    .join("\n");
}

export function ConfigPanel({
  node,
  availableFiles,
  availableColumns,
  onSave,
  onClose,
}: ConfigPanelProps) {
  const [draft, setDraft] = useState<Record<string, unknown>>(() => node?.data.config ?? {});
  const [mappingText, setMappingText] = useState(() => {
    if (!node || node.data.type !== "rename") {
      return "";
    }
    const mapping =
      node.data.config.mapping && typeof node.data.config.mapping === "object"
        ? (node.data.config.mapping as Record<string, unknown>)
        : {};
    return mappingToText(mapping);
  });
  const [aggregationsText, setAggregationsText] = useState(() => {
    if (!node || node.data.type !== "aggregate") {
      return "";
    }
    const raw = Array.isArray(node.data.config.aggregations)
      ? (node.data.config.aggregations as Array<Record<string, unknown>>)
      : [];
    return raw
      .map((agg) => `${asString(agg.column)}:${asString(agg.function)}`)
      .filter((line) => line !== ":")
      .join("\n");
  });

  const stepDefinition = useMemo(
    () => (node ? STEP_DEFINITIONS[node.data.type] : null),
    [node],
  );

  if (!node || !stepDefinition) {
    return (
      <aside className="w-80 shrink-0 border-l bg-card/40 p-3">
        <p className="text-xs text-muted-foreground">Select a step to configure it.</p>
      </aside>
    );
  }

  const update = (key: string, value: unknown) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  const handleColumnToggle = (key: string, column: string, checked: boolean) => {
    const current = asArray(draft[key]);
    const next = checked ? [...new Set([...current, column])] : current.filter((item) => item !== column);
    update(key, next);
  };

  const saveConfig = () => {
    let nextConfig = { ...draft };
    if (node.data.type === "rename") {
      nextConfig = { ...nextConfig, mapping: parseMapping(mappingText) };
    }
    if (node.data.type === "aggregate") {
      const aggregations = aggregationsText
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const [column, fn] = line.split(":").map((token) => token.trim());
          return { column, function: fn };
        })
        .filter((agg) => agg.column && agg.function);
      nextConfig = { ...nextConfig, aggregations };
    }
    onSave(node.id, nextConfig);
  };

  return (
    <aside className="w-80 shrink-0 border-l bg-card/40 p-3">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold">{node.data.label}</h4>
          <p className="text-xs text-muted-foreground">{stepDefinition.label} configuration</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded border px-2 py-1 text-xs hover:bg-muted"
        >
          Close
        </button>
      </div>

      <div className="space-y-3 text-xs">
        {node.data.type === "load" && (
          <label className="block space-y-1">
            <span className="text-muted-foreground">File</span>
            <select
              value={asString(draft.file_id)}
              onChange={(event) => update("file_id", event.target.value)}
              className="w-full rounded border bg-background px-2 py-1.5"
            >
              <option value="">Select file</option>
              {availableFiles.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.original_filename}
                </option>
              ))}
            </select>
          </label>
        )}

        {node.data.type === "filter" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Column</span>
              <select
                value={asString(draft.column)}
                onChange={(event) => update("column", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                <option value="">Select column</option>
                {availableColumns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Operator</span>
              <select
                value={asString(draft.operator) || "equals"}
                onChange={(event) => update("operator", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                {[
                  "equals",
                  "not_equals",
                  "greater_than",
                  "less_than",
                  "gte",
                  "lte",
                  "contains",
                  "not_contains",
                  "starts_with",
                  "ends_with",
                  "is_null",
                  "is_not_null",
                ].map((operator) => (
                  <option key={operator} value={operator}>
                    {operator}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Value</span>
              <input
                value={String(draft.value ?? "")}
                onChange={(event) => update("value", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
                placeholder="Filter value"
              />
            </label>
          </>
        )}

        {node.data.type === "join" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Join key</span>
              <select
                value={asString(draft.on)}
                onChange={(event) => update("on", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                <option value="">Select key</option>
                {availableColumns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Join type</span>
              <select
                value={asString(draft.how) || "inner"}
                onChange={(event) => update("how", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                {["inner", "left", "right", "outer"].map((how) => (
                  <option key={how} value={how}>
                    {how}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}

        {node.data.type === "aggregate" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Group by (comma-separated)</span>
              <input
                value={asArray(draft.group_by).join(", ")}
                onChange={(event) => update("group_by", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
                placeholder="region, product"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Aggregations (column:function per line)</span>
              <textarea
                value={aggregationsText}
                onChange={(event) => setAggregationsText(event.target.value)}
                className="min-h-24 w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]"
                placeholder={"amount:sum\norder_id:count"}
              />
            </label>
          </>
        )}

        {node.data.type === "sort" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Sort column</span>
              <select
                value={asString(draft.by)}
                onChange={(event) => update("by", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                <option value="">Select column</option>
                {availableColumns.map((column) => (
                  <option key={column} value={column}>
                    {column}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Order</span>
              <select
                value={asString(draft.order) || "asc"}
                onChange={(event) => update("order", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                <option value="asc">asc</option>
                <option value="desc">desc</option>
              </select>
            </label>
          </>
        )}

        {node.data.type === "select" && (
          <div className="space-y-1.5">
            <p className="text-muted-foreground">Columns</p>
            <div className="max-h-36 space-y-1 overflow-y-auto rounded border bg-background p-2">
              {availableColumns.map((column) => {
                const checked = asArray(draft.columns).includes(column);
                return (
                  <label key={column} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(event) =>
                        handleColumnToggle("columns", column, event.target.checked)
                      }
                    />
                    <span>{column}</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {node.data.type === "rename" && (
          <label className="block space-y-1">
            <span className="text-muted-foreground">Mappings (old:new per line)</span>
            <textarea
              value={mappingText}
              onChange={(event) => setMappingText(event.target.value)}
              className="min-h-24 w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]"
            />
          </label>
        )}

        {node.data.type === "save" && (
          <label className="block space-y-1">
            <span className="text-muted-foreground">Filename</span>
            <input
              value={asString(draft.filename)}
              onChange={(event) => update("filename", event.target.value)}
              className="w-full rounded border bg-background px-2 py-1.5"
              placeholder="output_report"
            />
          </label>
        )}

        {node.data.type === "pivot" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Index columns (comma-separated)</span>
              <input
                value={asArray(draft.index).join(", ")}
                onChange={(event) => update("index", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Columns field</span>
              <input
                value={asString(draft.columns)}
                onChange={(event) => update("columns", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Values field</span>
              <input
                value={asString(draft.values)}
                onChange={(event) => update("values", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Aggregate function</span>
              <select
                value={asString(draft.aggfunc) || "sum"}
                onChange={(event) => update("aggfunc", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                {["sum", "mean", "count", "min", "max"].map((fn) => (
                  <option key={fn} value={fn}>
                    {fn}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}

        {node.data.type === "unpivot" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">ID variables (comma-separated)</span>
              <input
                value={asArray(draft.id_vars).join(", ")}
                onChange={(event) => update("id_vars", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Value variables (comma-separated)</span>
              <input
                value={asArray(draft.value_vars).join(", ")}
                onChange={(event) => update("value_vars", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Variable column name</span>
              <input
                value={asString(draft.var_name) || "variable"}
                onChange={(event) => update("var_name", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Value column name</span>
              <input
                value={asString(draft.value_name) || "value"}
                onChange={(event) => update("value_name", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
          </>
        )}

        {node.data.type === "deduplicate" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Subset columns (comma-separated, optional)</span>
              <input
                value={asArray(draft.subset).join(", ")}
                onChange={(event) => update("subset", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Keep</span>
              <select
                value={asString(draft.keep) || "first"}
                onChange={(event) => update("keep", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                {["first", "last", "false"].map((keep) => (
                  <option key={keep} value={keep}>
                    {keep}
                  </option>
                ))}
              </select>
            </label>
          </>
        )}

        {node.data.type === "fill_nulls" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Strategy</span>
              <select
                value={asString(draft.strategy) || "constant"}
                onChange={(event) => update("strategy", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              >
                {["constant", "ffill", "bfill"].map((strategy) => (
                  <option key={strategy} value={strategy}>
                    {strategy}
                  </option>
                ))}
              </select>
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Constant value</span>
              <input
                value={String(draft.constant_value ?? "")}
                onChange={(event) => update("constant_value", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
                placeholder="0"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Columns (comma-separated, optional)</span>
              <input
                value={asArray(draft.columns).join(", ")}
                onChange={(event) => update("columns", parseCsv(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
          </>
        )}

        {node.data.type === "sample" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Sample size (n)</span>
              <input
                type="number"
                min={1}
                value={asNumber(draft.n, 1000)}
                onChange={(event) => update("n", Number(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Fraction (optional)</span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={typeof draft.fraction === "number" ? draft.fraction : ""}
                onChange={(event) =>
                  update("fraction", event.target.value ? Number(event.target.value) : undefined)
                }
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Random state</span>
              <input
                type="number"
                value={asNumber(draft.random_state, 42)}
                onChange={(event) => update("random_state", Number(event.target.value))}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
          </>
        )}

        {node.data.type === "validate" && (
          <label className="block space-y-1">
            <span className="text-muted-foreground">Validation rules (JSON)</span>
            <textarea
              value={JSON.stringify(draft.rules ?? [], null, 2)}
              onChange={(event) => {
                try {
                  update("rules", JSON.parse(event.target.value));
                } catch {
                  update("rules", event.target.value);
                }
              }}
              className="min-h-24 w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]"
            />
          </label>
        )}

        {node.data.type === "transform" && (
          <>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Column</span>
              <input
                value={asString(draft.column)}
                onChange={(event) => update("column", event.target.value)}
                className="w-full rounded border bg-background px-2 py-1.5"
              />
            </label>
            <label className="block space-y-1">
              <span className="text-muted-foreground">Expression</span>
              <textarea
                value={asString(draft.expression)}
                onChange={(event) => update("expression", event.target.value)}
                className="min-h-20 w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]"
              />
            </label>
          </>
        )}

        {node.data.type === "sql" && (
          <label className="block space-y-1">
            <span className="text-muted-foreground">SQL query</span>
            <textarea
              value={asString(draft.query)}
              onChange={(event) => update("query", event.target.value)}
              className="min-h-24 w-full rounded border bg-background px-2 py-1.5 font-mono text-[11px]"
            />
          </label>
        )}
      </div>

      <div className="mt-4 flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded border px-2.5 py-1.5 text-xs hover:bg-muted"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={saveConfig}
          className="rounded border border-primary bg-primary px-2.5 py-1.5 text-xs text-primary-foreground hover:opacity-90"
        >
          Save
        </button>
      </div>
    </aside>
  );
}
