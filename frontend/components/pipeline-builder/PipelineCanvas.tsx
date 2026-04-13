import { useCallback, useMemo, useState, type DragEvent } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type ReactFlowInstance,
  type XYPosition,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { isVisualStepType, type VisualStepType } from "@/lib/stepDefinitions";
import type { BuilderEdge, BuilderNode } from "@/lib/yamlGraphSync";
import { StepNode } from "./StepNode";

interface PipelineCanvasProps {
  nodes: BuilderNode[];
  edges: BuilderEdge[];
  onNodesChange: (changes: NodeChange<BuilderNode>[]) => void;
  onEdgesChange: (changes: EdgeChange<BuilderEdge>[]) => void;
  onConnect: (connection: Connection) => void;
  onDropStep: (stepType: VisualStepType, position: XYPosition) => void;
  onConfigureNode: (nodeId: string) => void;
  onDeleteNode: (nodeId: string) => void;
}

const nodeTypes = {
  stepNode: StepNode,
};

export function PipelineCanvas({
  nodes,
  edges,
  onNodesChange,
  onEdgesChange,
  onConnect,
  onDropStep,
  onConfigureNode,
  onDeleteNode,
}: PipelineCanvasProps) {
  const [instance, setInstance] = useState<ReactFlowInstance<BuilderNode, BuilderEdge> | null>(
    null,
  );

  const decoratedNodes = useMemo(
    () =>
      nodes.map((node) => ({
        ...node,
        data: {
          ...node.data,
          onConfigure: onConfigureNode,
          onDelete: onDeleteNode,
        },
      })),
    [nodes, onConfigureNode, onDeleteNode],
  );

  const handleDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const handleDrop = useCallback(
    (event: DragEvent) => {
      event.preventDefault();
      if (!instance) {
        return;
      }

      const droppedStep =
        event.dataTransfer.getData("application/pipeline-step") ||
        event.dataTransfer.getData("text/plain");
      if (!isVisualStepType(droppedStep)) {
        return;
      }

      const position = instance.screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });

      onDropStep(droppedStep, position);
    },
    [instance, onDropStep],
  );

  return (
    <div className="relative h-full w-full rounded-md border bg-background/30" data-testid="pipeline-canvas">
      <ReactFlow<BuilderNode, BuilderEdge>
        nodes={decoratedNodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onInit={setInstance}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} />
        <Controls />
        <MiniMap
          nodeColor="#64748b"
          maskColor="rgba(15, 23, 42, 0.4)"
          style={{ background: "var(--card)", border: "1px solid var(--border)" }}
        />
      </ReactFlow>

      {nodes.length === 0 && (
        <div
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
          data-testid="pipeline-canvas-empty"
        >
          <p className="rounded border bg-card px-3 py-1.5 text-xs text-muted-foreground">
            Drag steps from the palette to start building.
          </p>
        </div>
      )}
    </div>
  );
}
