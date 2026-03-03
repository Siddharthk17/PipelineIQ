import React from "react";
import { Handle, Position } from "@xyflow/react";
import { Settings2 } from "lucide-react";
import { useImpactState } from "../ImpactContext";

export function StepNode({ id, data }: { id: string; data: any }) {
  const { clickedNodeId, affectedSteps } = useImpactState();

  let impact: string | undefined;
  if (clickedNodeId) {
    if (id === clickedNodeId) impact = "clicked";
    else if (data.stepName && affectedSteps.has(data.stepName)) impact = "affected";
    else impact = "dimmed";
  }

  const style: React.CSSProperties = {
    background: 'var(--bg-elevated)',
    border: '1px solid var(--widget-border)',
    transition: 'all 0.3s ease',
  };
  if (impact === 'clicked') {
    style.border = '2px solid var(--accent-error)';
    style.transform = 'scale(1.05)';
    style.boxShadow = '0 0 12px rgba(255,80,80,0.3)';
    style.zIndex = 10;
  } else if (impact === 'affected') {
    style.border = '2px solid var(--accent-warning)';
    style.background = 'color-mix(in srgb, var(--accent-warning) 20%, var(--bg-elevated))';
  } else if (impact === 'dimmed') {
    style.opacity = 0.15;
  }

  return (
    <div className="px-4 py-2 shadow-md rounded-md min-w-[150px]" style={style}>
      <Handle type="target" position={Position.Left} className="w-3 h-3 bg-[var(--accent-secondary)] border-2 border-[var(--bg-base)]" />
      <div className="flex items-center gap-2 mb-2">
        <div className="p-1.5 rounded text-[var(--accent-secondary)]" style={{ background: 'color-mix(in srgb, var(--accent-secondary) 20%, transparent)' }}>
          <Settings2 className="w-4 h-4" />
        </div>
        <div className="font-bold text-sm text-[var(--text-primary)]">{data.label}</div>
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="px-1.5 py-0.5 rounded text-[var(--text-secondary)] uppercase" style={{ background: 'var(--bg-surface)' }}>{data.type}</span>
        <span className="text-[var(--text-secondary)] font-mono">{data.rowsIn} → {data.rowsOut}</span>
      </div>
      <Handle type="source" position={Position.Right} className="w-3 h-3 bg-[var(--accent-secondary)] border-2 border-[var(--bg-base)]" />
    </div>
  );
}
