import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { STEP_DEFINITIONS } from "@/lib/stepDefinitions";
import type { BuilderNode } from "@/lib/yamlGraphSync";

function StepNodeComponent({ id, data, selected }: NodeProps<BuilderNode>) {
  const definition = STEP_DEFINITIONS[data.type];

  return (
    <div
      data-testid={`step-node-${id}`}
      className={[
        "min-w-[190px] rounded-lg border px-3 py-2 shadow-sm transition-all",
        selected ? "ring-2 ring-[var(--accent-primary)]" : "ring-0",
      ].join(" ")}
      style={{
        borderColor: "var(--widget-border)",
        backgroundColor: "var(--bg-elevated)",
        color: "var(--text-primary)",
      }}
    >
      {definition.maxInputs > 0 && data.type !== "join" && (
        <Handle
          id="input"
          type="target"
          position={Position.Left}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-primary)" }}
        />
      )}
      {data.type === "join" && (
        <>
          <Handle
            id="left"
            type="target"
            position={Position.Left}
            className="h-3 w-3 border-2"
            style={{
              top: "34%",
              borderColor: "var(--bg-base)",
              backgroundColor: "var(--accent-primary)",
            }}
          />
          <Handle
            id="right"
            type="target"
            position={Position.Left}
            className="h-3 w-3 border-2"
            style={{
              top: "66%",
              borderColor: "var(--bg-base)",
              backgroundColor: "var(--accent-primary)",
            }}
          />
        </>
      )}
      {data.type !== "save" && (
        <Handle
          id="output"
          type="source"
          position={Position.Right}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-secondary)" }}
        />
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-sm font-semibold leading-none">{data.label}</p>
          <p className="text-xs text-[var(--text-secondary)]">{definition.label}</p>
        </div>
        {!data.backendSupported && (
          <span
            className="rounded border px-1.5 py-0.5 text-[10px] uppercase tracking-wide"
            style={{
              borderColor: "color-mix(in srgb, var(--accent-warning) 45%, transparent)",
              color: "var(--accent-warning)",
              backgroundColor: "color-mix(in srgb, var(--accent-warning) 14%, transparent)",
            }}
          >
            Visual only
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center justify-end gap-1">
        <button
          type="button"
          onClick={() => data.onConfigure?.(id)}
          data-testid={`config-btn-${id}`}
          aria-label={`Configure ${data.label}`}
          className="rounded border px-2 py-1 text-[11px] transition-colors hover:bg-[var(--interactive-hover)]"
          style={{ borderColor: "var(--widget-border)" }}
        >
          ⚙
        </button>
        <button
          type="button"
          onClick={() => data.onDelete?.(id)}
          data-testid={`delete-btn-${id}`}
          aria-label={`Delete ${data.label}`}
          className="rounded border px-2 py-1 text-[11px] text-[var(--accent-error)] transition-colors hover:bg-[var(--interactive-hover)]"
          style={{ borderColor: "color-mix(in srgb, var(--accent-error) 45%, transparent)" }}
        >
          ×
        </button>
      </div>
    </div>
  );
}

export const StepNode = memo(StepNodeComponent);
