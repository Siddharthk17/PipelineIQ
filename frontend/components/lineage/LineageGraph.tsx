import React, { useCallback, useState, useEffect, useRef, useMemo } from "react";
import { ReactFlow, MiniMap, Controls, Background, useNodesState, useEdgesState, BackgroundVariant } from "@xyflow/react";
import type { Node as ReactFlowNode, Edge as ReactFlowEdge } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useLineageGraph } from "@/hooks/useLineage";
import { getImpactAnalysis } from "@/lib/api";
import { ImpactAnalysis } from "@/lib/types";
import { ImpactProvider, ImpactState } from "./ImpactContext";
import { SourceFileNode } from "./nodes/SourceFileNode";
import { StepNode } from "./nodes/StepNode";
import { ColumnNode } from "./nodes/ColumnNode";
import { OutputFileNode } from "./nodes/OutputFileNode";
import { LineageSidebar } from "./LineageSidebar";

const nodeTypes = {
  sourceFile: SourceFileNode,
  stepNode: StepNode,
  columnNode: ColumnNode,
  outputFile: OutputFileNode,
};

interface LineageGraphProps {
  runId: string | null;
  mode: "ancestry" | "impact";
}

export function LineageGraph({ runId, mode }: LineageGraphProps) {
  const { data: graphData, isLoading } = useLineageGraph(runId);
  const [nodes, setNodes, onNodesChange] = useNodesState<ReactFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<ReactFlowEdge>([]);

  const [selectedNode, setSelectedNode] = useState<ReactFlowNode | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [impactData, setImpactData] = useState<ImpactAnalysis | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(null);
  const baseEdgesRef = useRef<ReactFlowEdge[]>([]);

  // Impact highlighting state — drives context for node components
  const [clickedNodeId, setClickedNodeId] = useState<string | null>(null);
  const [affectedSteps, setAffectedSteps] = useState<Set<string>>(new Set());
  const [clickedStepName, setClickedStepName] = useState<string | null>(null);

  const impactState = useMemo<ImpactState>(() => ({
    clickedNodeId,
    affectedSteps,
    clickedStepName,
  }), [clickedNodeId, affectedSteps, clickedStepName]);

  // Refs for values needed in callbacks — eliminates stale closures
  const modeRef = useRef(mode);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  const runIdRef = useRef(runId);
  useEffect(() => { runIdRef.current = runId; }, [runId]);
  const impactLoadingRef = useRef(false);

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  useEffect(() => {
    if (graphData) {
      baseEdgesRef.current = graphData.edges;
      setNodes(graphData.nodes);
      setEdges(graphData.edges);
    }
  }, [graphData, setNodes, setEdges]);

  const resetGraph = useCallback(() => {
    // Reset impact context state
    setClickedNodeId(null);
    setAffectedSteps(new Set());
    setClickedStepName(null);
    // Restore original edge styles
    setEdges(baseEdgesRef.current);
    setSelectedNode(null);
    setSidebarOpen(false);
    setImpactData(null);
  }, [setEdges]);

  // Reset when switching modes
  useEffect(() => {
    resetGraph();
  }, [mode, resetGraph]);

  const applyImpactHighlight = useCallback((clickedNode: ReactFlowNode, impact: ImpactAnalysis) => {
    const steps = new Set(impact.affected_steps ?? []);
    const stepName = clickedNode.data?.stepName as string;

    // Update context state — node components re-render via useImpactState()
    setClickedNodeId(clickedNode.id);
    setAffectedSteps(steps);
    setClickedStepName(stepName);

    // Build set of affected node IDs for edge highlighting
    const affectedNodeIds = new Set<string>();
    nodes.forEach(n => {
      const ns = n.data?.stepName as string | undefined;
      if (n.id === clickedNode.id || (ns && (steps.has(ns) || ns === stepName))) {
        affectedNodeIds.add(n.id);
      }
    });

    setEdges(prev => prev.map(e => {
      if (affectedNodeIds.has(e.source) && affectedNodeIds.has(e.target)) {
        return { ...e, style: { stroke: 'var(--accent-error)', strokeWidth: 2, opacity: 1 }, animated: true };
      }
      return { ...e, style: { ...e.style, opacity: 0.1 }, animated: false };
    }));
  }, [setEdges, nodes]);

  const onNodeClick = useCallback((_: React.MouseEvent, node: ReactFlowNode) => {
    const currentMode = modeRef.current;
    const currentRunId = runIdRef.current;

    if (currentMode === "ancestry") {
      if (node.type === "columnNode") {
        setSelectedNode(node);
        setImpactData(null);
        setSidebarOpen(true);
      } else {
        setSelectedNode(null);
        setSidebarOpen(false);
      }
      return;
    }

    // Impact mode
    if (node.type !== "columnNode") {
      showToast("Select a column node to see its impact");
      return;
    }

    if (impactLoadingRef.current) return;

    const stepName = node.data?.stepName as string;
    const columnName = node.data?.columnName as string;
    if (!currentRunId || !stepName || !columnName) return;

    impactLoadingRef.current = true;
    setImpactLoading(true);
    setSelectedNode(node);

    getImpactAnalysis(currentRunId, stepName, columnName)
      .then((impact) => {
        setImpactData(impact);
        setSidebarOpen(true);
        applyImpactHighlight(node, impact);
      })
      .catch(() => {
        showToast("Could not load impact analysis for this column");
        resetGraph();
      })
      .finally(() => {
        impactLoadingRef.current = false;
        setImpactLoading(false);
      });
  }, [showToast, applyImpactHighlight, resetGraph]);

  const onPaneClick = useCallback(() => {
    resetGraph();
  }, [resetGraph]);

  if (!runId) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--text-secondary)]">
        Select a pipeline run to view its lineage graph.
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-[var(--text-secondary)]">
        Loading graph...
      </div>
    );
  }

  return (
    <div className="relative w-full h-full">
      <ImpactProvider value={impactState}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          className="bg-[var(--bg-base)]"
          style={{ background: 'var(--bg-base)' }}
          minZoom={0.1}
          maxZoom={4}
          defaultEdgeOptions={{
            style: {
              stroke: 'var(--accent-primary)',
              strokeWidth: 1.5,
              opacity: 0.6,
            },
          }}
        >
          <Background color="var(--widget-border)" gap={16} variant={BackgroundVariant.Dots} />
          <Controls className="bg-[var(--bg-surface)] border-[var(--widget-border)] fill-[var(--text-primary)]" />
          <MiniMap
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--widget-border)',
            }}
            nodeColor={(n) => {
              if (n.type === 'sourceFile') return 'var(--accent-primary)';
              if (n.type === 'stepNode') return 'var(--accent-secondary)';
              if (n.type === 'outputFile') return 'var(--accent-success)';
              return 'var(--text-secondary)';
            }}
            maskColor="rgba(0,0,0,0.3)"
          />
        </ReactFlow>
      </ImpactProvider>

      {impactLoading && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg text-xs font-medium z-20"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--widget-border)', color: 'var(--text-secondary)' }}>
          <span className="animate-pulse">Analyzing impact…</span>
        </div>
      )}

      {toast && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg text-xs font-medium z-20 shadow-lg"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--widget-border)', color: 'var(--text-primary)' }}>
          {toast}
        </div>
      )}

      <LineageSidebar
        runId={runId}
        mode={mode}
        step={selectedNode?.data?.stepName as string || null}
        column={mode === "ancestry" ? (selectedNode?.data?.label as string || null) : (selectedNode?.data?.columnName as string || null)}
        impactData={impactData}
        isOpen={sidebarOpen}
        onClose={() => { setSidebarOpen(false); if (mode === "impact") resetGraph(); }}
      />
    </div>
  );
}
