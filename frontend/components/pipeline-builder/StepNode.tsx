import React, { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { STEP_DEFINITIONS } from "@/lib/stepDefinitions";
import type { BuilderNode } from "@/lib/yamlGraphSync";
import type { CollaboratorState } from "@/hooks/useCollaborativePipeline";

function StepNodeComponent({ id, data, selected }: NodeProps<BuilderNode>) {
  const definition = STEP_DEFINITIONS[data.type];
  const collaborators: CollaboratorState[] =
    (data as Record<string, unknown>).collaborators as CollaboratorState[] ?? [];
  const remoteSelector = collaborators.find(
    (c) => c.selectedNode === id && c.user,
  );

  if (!definition) {
    return (
      <div
        data-testid={`step-node-${id}`}
        className="min-w-[190px] rounded-lg border px-3 py-2 text-xs text-[var(--text-secondary)]"
        style={{
          borderColor: "var(--widget-border)",
          backgroundColor: "var(--bg-elevated)",
        }}
      >
        <Handle
          id="input"
          type="target"
          position={Position.Left}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-primary)" }}
        />
        <p>Unknown type: {String(data.type)}</p>
        <Handle
          id="output"
          type="source"
          position={Position.Right}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-secondary)" }}
        />
      </div>
    );
  }

  const hasError = typeof (data as Record<string, unknown>).validationError === "string"
    && ((data as Record<string, unknown>).validationError as string).length > 0;
  const inferredSchema = (data as Record<string, unknown>).inferredSchema as string[] | undefined;
  const outputSchema = (data as Record<string, unknown>).outputSchema as string[] | undefined;

  return (
    <div
      data-testid={`step-node-${id}`}
      className={[
        "relative",
        "min-w-[190px] rounded-lg border px-3 py-2 shadow-sm transition-all",
        selected ? "ring-2 ring-[var(--accent-primary)]" : "ring-0",
        hasError ? "border-[var(--accent-error)]" : "",
      ].join(" ")}
      style={{
        borderColor: hasError ? "var(--accent-error)" : "var(--widget-border)",
        backgroundColor: "var(--bg-elevated)",
        color: "var(--text-primary)",
      }}
    >
      {remoteSelector && (
        <div
          className="step-node-remote-selection"
          style={{
            position: "absolute",
            inset: "-3px",
            borderRadius: "10px",
            border: `2px solid ${remoteSelector.user.color}`,
            pointerEvents: "none",
            zIndex: 10,
          }}
          title={`${remoteSelector.user.name} is viewing this step`}
          data-testid={`remote-selection-${id}`}
        />
      )}

      {/* Input handles */}
      {!definition.isSource && data.type !== "join" && (
        <Handle
          id="input"
          type="target"
          position={Position.Left}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-primary)" }}
          data-testid={`handle-${id}-in`}
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
            data-testid={`handle-${id}-left`}
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
            data-testid={`handle-${id}-right`}
          />
        </>
      )}

      {/* Output handle */}
      {!definition.isTerminal && (
        <Handle
          id="output"
          type="source"
          position={Position.Right}
          className="h-3 w-3 border-2"
          style={{ borderColor: "var(--bg-base)", backgroundColor: "var(--accent-secondary)" }}
          data-testid={`handle-${id}-out`}
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

      {inferredSchema && (
        <div
          className="mt-1.5 border-t pt-1.5 text-[10px] text-[var(--text-secondary)]"
          style={{ borderColor: "var(--widget-border)" }}
          data-testid={`schema-hint-${id}`}
        >
          {inferredSchema.length}
          {" \u2192 "}
          {outputSchema != null ? outputSchema.length : "?"} cols
        </div>
      )}

      {hasError && (
        <div
          className="mt-1 text-[10px] text-[var(--accent-error)]"
          data-testid={`error-badge-${id}`}
        >
          {(data as Record<string, unknown>).validationError as string}
        </div>
      )}

      <div className="mt-2 flex items-center justify-end gap-1">
        <button
          type="button"
          onClick={() => data.onConfigure?.(id)}
          data-testid={`config-btn-${id}`}
          aria-label={`Configure ${data.label}`}
          className="rounded border px-2 py-1 text-[11px] transition-colors hover:bg-[var(--interactive-hover)]"
          style={{ borderColor: "var(--widget-border)" }}
        >
          \u2699
        </button>
        <button
          type="button"
          onClick={() => data.onDelete?.(id)}
          data-testid={`delete-btn-${id}`}
          aria-label={`Delete ${data.label}`}
          className="rounded border px-2 py-1 text-[11px] text-[var(--accent-error)] transition-colors hover:bg-[var(--interactive-hover)]"
          style={{ borderColor: "color-mix(in srgb, var(--accent-error) 45%, transparent)" }}
        >
          \u00D7
        </button>
      </div>
    </div>
  );
}

export const StepNode = memo(StepNodeComponent);
