"use client";

import React from "react";
import { useWidgetStore, LayoutNode, getAllWidgets } from "@/store/widgetStore";
import { useIsMobile } from "@/hooks/use-mobile";
import { WidgetShell } from "../widgets/WidgetShell";

import { QuickStatsWidget } from "../widgets/QuickStatsWidget";
import { FileRegistryWidget } from "../widgets/FileRegistryWidget";
import { FileUploadWidget } from "../widgets/FileUploadWidget";
import { PipelineEditorWidget } from "../widgets/PipelineEditorWidget";
import { RunMonitorWidget } from "../widgets/RunMonitorWidget";
import { LineageGraphWidget } from "../widgets/LineageGraphWidget";
import { RunHistoryWidget } from "../widgets/RunHistoryWidget";
import { VersionHistoryWidget } from "../widgets/VersionHistoryWidget";

// Widget components have heterogeneous props; Record<string, React.FC<any>> is the simplest safe mapping
const WIDGET_COMPONENTS: Record<string, React.FC<any>> = {
  "quick-stats": QuickStatsWidget,
  "file-registry": FileRegistryWidget,
  "file-upload": FileUploadWidget,
  "pipeline-editor": PipelineEditorWidget,
  "run-monitor": RunMonitorWidget,
  "lineage-graph": LineageGraphWidget,
  "run-history": RunHistoryWidget,
  "version-history": VersionHistoryWidget,
};

function DwindleNode({ node }: { node: LayoutNode }) {
  const { widgets, activeWidgetId, setActiveWidget } = useWidgetStore();

  if (node.type === 'widget') {
    const config = widgets.find(w => w.id === node.id);
    const Component = WIDGET_COMPONENTS[node.id];
    if (!config || !Component) return null;

    return (
      <WidgetShell
        config={config}
        isActive={activeWidgetId === node.id}
        onClick={() => setActiveWidget(node.id)}
        onPointerEnter={() => setActiveWidget(node.id)}
        onPointerMove={() => setActiveWidget(node.id)}
      >
        <Component />
      </WidgetShell>
    );
  }

  return (
    <div className={`flex-1 flex gap-[var(--grid-gap)] min-w-0 min-h-0 ${node.direction === "row" ? "flex-row" : "flex-col"}`}>
      <div className="flex-1 flex min-w-0 min-h-0">
        {node.first && <DwindleNode node={node.first} />}
      </div>
      <div className="flex-1 flex min-w-0 min-h-0">
        {node.second && <DwindleNode node={node.second} />}
      </div>
    </div>
  );
}

function MobileWidgetList({ node }: { node: LayoutNode }) {
  const { widgets, activeWidgetId, setActiveWidget } = useWidgetStore();
  const ids = getAllWidgets(node);

  return (
    <div className="flex flex-col gap-[var(--grid-gap)] w-full">
      {ids.map((id) => {
        const config = widgets.find(w => w.id === id);
        const Component = WIDGET_COMPONENTS[id];
        if (!config || !Component) return null;

        return (
          <div key={id} className="w-full" style={{ minHeight: "320px" }}>
            <WidgetShell
              config={config}
              isActive={activeWidgetId === id}
              onClick={() => setActiveWidget(id)}
              onPointerEnter={() => setActiveWidget(id)}
              onPointerMove={() => setActiveWidget(id)}
            >
              <Component />
            </WidgetShell>
          </div>
        );
      })}
    </div>
  );
}

export function WidgetGrid() {
  const { workspaces, activeWorkspaceId, switchWorkspace } = useWidgetStore();
  const layout = workspaces[activeWorkspaceId];
  const isMobile = useIsMobile();

  return (
    <div className="w-full h-full flex flex-col bg-[var(--grid-bg)] overflow-hidden">
      <div className={`flex-1 p-[var(--grid-gap)] ${isMobile ? "overflow-y-auto" : "overflow-hidden flex"}`}>
        {!layout ? (
          <div className="flex items-center justify-center w-full h-full text-[var(--text-secondary)] font-mono text-sm">
            Workspace {activeWorkspaceId} is empty. Press <kbd className="px-2 py-1 mx-2 rounded bg-[var(--bg-surface)] border border-[var(--widget-border)]">Alt+Enter</kbd> to launch.
          </div>
        ) : isMobile ? (
          <MobileWidgetList node={layout} />
        ) : (
          <DwindleNode node={layout} />
        )}
      </div>
    </div>
  );
}
