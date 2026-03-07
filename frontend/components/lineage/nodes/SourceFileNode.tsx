import React from "react";
import { Handle, Position } from "@xyflow/react";
import { Database } from "lucide-react";
import { useImpactState } from "../ImpactContext";

export function SourceFileNode({ id, data }: { id: string; data: any }) {
  const { clickedNodeId, affectedSteps } = useImpactState();

  let impact: string | undefined;
  if (clickedNodeId) {
    if (id === clickedNodeId) impact = "clicked";
    else if (data.stepName && affectedSteps.has(data.stepName)) impact = "affected";
    else impact = "dimmed";
  }

  const style: React.CSSProperties = {
    background: 'color-mix(in srgb, var(--accent-primary) 15%, transparent)',
    border: '1px solid var(--accent-primary)',
    transition: 'all 0.3s ease',
  };
  if (impact === 'clicked') {
    style.border = '2px solid var(--accent-error)';
    style.transform = 'scale(1.05)';
    style.boxShadow = '0 0 12px rgba(255,80,80,0.3)';
  } else if (impact === 'affected') {
    style.border = '2px solid var(--accent-warning)';
    style.background = 'color-mix(in srgb, var(--accent-warning) 20%, transparent)';
  } else if (impact === 'dimmed') {
    style.opacity = 0.15;
  }

  return (
    <div className="px-4 py-2 shadow-md rounded-md min-w-[150px]" style={style}>
      <div className="flex items-center gap-2 mb-2">
        <div className="p-1.5 rounded text-[var(--accent-primary)]" style={{ background: 'color-mix(in srgb, var(--accent-primary) 25%, transparent)' }}>
          <Database className="w-4 h-4" />
        </div>
        <div className="font-bold text-sm text-[var(--text-primary)]">{data.label}</div>
      </div>
      <div className="text-xs text-[var(--text-secondary)]">{data.rowCount} rows</div>
      <Handle type="source" position={Position.Right} className="w-3 h-3 bg-[var(--accent-primary)] border-2 border-[var(--bg-base)]" />
    </div>
  );
}
