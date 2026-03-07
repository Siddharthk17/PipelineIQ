import React from "react";
import { Handle, Position } from "@xyflow/react";
import { useImpactState } from "../ImpactContext";

export function ColumnNode({ id, data }: { id: string; data: any }) {
  const { clickedNodeId, affectedSteps } = useImpactState();

  let impact: string | undefined;
  if (clickedNodeId) {
    if (id === clickedNodeId) impact = "clicked";
    else if (data.stepName && affectedSteps.has(data.stepName)) impact = "affected";
    else impact = "dimmed";
  }

  const style: React.CSSProperties = {
    background: 'var(--bg-surface)',
    border: '1px solid var(--widget-border)',
    fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
    transition: 'all 0.3s ease',
  };
  if (impact === 'clicked') {
    style.border = '2px solid var(--accent-error)';
    style.transform = 'scale(1.05)';
    style.boxShadow = '0 0 12px rgba(255,80,80,0.3)';
    style.zIndex = 10;
  } else if (impact === 'affected') {
    style.border = '2px solid var(--accent-warning)';
    style.background = 'color-mix(in srgb, var(--accent-warning) 20%, var(--bg-surface))';
  } else if (impact === 'dimmed') {
    style.opacity = 0.15;
  }

  return (
    <div className="px-3 py-1.5 shadow-sm rounded min-w-[120px] flex items-center justify-between gap-3" style={style}>
      <Handle type="target" position={Position.Left} className="w-2 h-2 bg-[var(--text-secondary)] border-none" />
      <div className="font-mono text-xs text-[var(--text-secondary)] truncate" title={data.label}>{data.label}</div>
      <div className="text-[9px] px-1 py-0.5 rounded text-[var(--text-secondary)] uppercase" style={{ background: 'var(--bg-elevated)' }}>{data.dataType}</div>
      <Handle type="source" position={Position.Right} className="w-2 h-2 bg-[var(--text-secondary)] border-none" />
    </div>
  );
}
