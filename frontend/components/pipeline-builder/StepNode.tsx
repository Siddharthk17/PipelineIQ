import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { STEP_DEFINITIONS } from "@/lib/stepDefinitions";
import type { BuilderNode } from "@/lib/yamlGraphSync";

function StepNodeComponent({ id, data, selected }: NodeProps<BuilderNode>) {
  const definition = STEP_DEFINITIONS[data.type];

  return (
    <div
      className={[
        "min-w-[190px] rounded-lg border bg-card px-3 py-2 text-card-foreground shadow-sm",
        selected ? "ring-2 ring-primary" : "ring-0",
      ].join(" ")}
    >
      {definition.maxInputs > 0 && data.type !== "join" && (
        <Handle
          id="input"
          type="target"
          position={Position.Left}
          className="h-3 w-3 border-2 border-border bg-background"
        />
      )}
      {data.type === "join" && (
        <>
          <Handle
            id="left"
            type="target"
            position={Position.Left}
            style={{ top: "34%" }}
            className="h-3 w-3 border-2 border-border bg-background"
          />
          <Handle
            id="right"
            type="target"
            position={Position.Left}
            style={{ top: "66%" }}
            className="h-3 w-3 border-2 border-border bg-background"
          />
        </>
      )}
      {data.type !== "save" && (
        <Handle
          id="output"
          type="source"
          position={Position.Right}
          className="h-3 w-3 border-2 border-border bg-background"
        />
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="space-y-0.5">
          <p className="text-sm font-semibold leading-none">{data.label}</p>
          <p className="text-xs text-muted-foreground">{definition.label}</p>
        </div>
        {!data.backendSupported && (
          <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-700">
            Visual only
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center justify-end gap-1">
        <button
          type="button"
          onClick={() => data.onConfigure?.(id)}
          className="rounded border border-border px-2 py-1 text-[11px] hover:bg-muted"
        >
          Configure
        </button>
        <button
          type="button"
          onClick={() => data.onDelete?.(id)}
          className="rounded border border-destructive/30 px-2 py-1 text-[11px] text-destructive hover:bg-destructive/10"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export const StepNode = memo(StepNodeComponent);
