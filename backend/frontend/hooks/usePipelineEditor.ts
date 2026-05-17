import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import {
  addEdge,
  useEdgesState,
  useNodesState,
  type Connection,
  type EdgeChange,
  type NodeChange,
  type XYPosition,
} from "@xyflow/react";
import { useDebouncedCallback } from "use-debounce";
import type { UploadedFile } from "@/lib/types";
import { STEP_DEFINITIONS, getDefaultStepConfig, isVisualStepType, type VisualStepType } from "@/lib/stepDefinitions";
import { graphToYAML, type BuilderEdge, type BuilderGraph, type BuilderNode, yamlToGraph } from "@/lib/yamlGraphSync";
import { topologicalSort } from "@/lib/topologicalSort";

interface UsePipelineEditorOptions {
  yamlText: string;
  onYamlTextChange: (nextYaml: string) => void;
  availableFiles: UploadedFile[];
}

export interface ConnectionValidationResult {
  valid: boolean;
  targetHandle?: string;
  message?: string;
}

function toEdgeId(source: string, target: string, targetHandle?: string): string {
  return `e-${source}-${target}-${targetHandle ?? "input"}`;
}

export function validateConnectionCandidate(
  connection: Connection,
  nodes: BuilderNode[],
  edges: BuilderEdge[],
): ConnectionValidationResult {
  const source = connection.source;
  const target = connection.target;
  if (!source || !target) {
    return { valid: false, message: "Invalid connection endpoints." };
  }

  if (source === target) {
    return { valid: false, message: "A step cannot connect to itself." };
  }

  const sourceNode = nodes.find((node) => node.id === source);
  const targetNode = nodes.find((node) => node.id === target);
  if (!sourceNode || !targetNode) {
    return { valid: false, message: "Connection references unknown step nodes." };
  }

  if (sourceNode.data.type === "save") {
    return { valid: false, message: "Save steps are terminal and cannot feed downstream steps." };
  }

  const sourceDef = STEP_DEFINITIONS[sourceNode.data.type];
  const targetDef = STEP_DEFINITIONS[targetNode.data.type];
  if (!sourceDef || !targetDef) {
    return { valid: false, message: "Unsupported step type in connection." };
  }

  if (targetDef.maxInputs === 0) {
    return { valid: false, message: `${targetDef.label} does not accept upstream inputs.` };
  }

  const incomingToTarget = edges.filter((edge) => edge.target === target);
  let targetHandle = connection.targetHandle ?? "input";

  if (targetNode.data.type === "join") {
    const usedHandles = new Set(
      incomingToTarget
        .map((edge) => edge.targetHandle)
        .filter((handle): handle is string => typeof handle === "string"),
    );

    if (targetHandle !== "left" && targetHandle !== "right") {
      if (!usedHandles.has("left")) {
        targetHandle = "left";
      } else if (!usedHandles.has("right")) {
        targetHandle = "right";
      } else {
        return { valid: false, message: "Join steps accept only two inputs (left and right)." };
      }
    }

    if (usedHandles.has(targetHandle)) {
      return { valid: false, message: `Join ${targetHandle} input is already connected.` };
    }

    if (incomingToTarget.length >= 2) {
      return { valid: false, message: "Join steps accept only two inputs." };
    }
  } else {
    targetHandle = "input";
    if (incomingToTarget.length >= targetDef.maxInputs) {
      return {
        valid: false,
        message: `${targetDef.label} accepts at most ${targetDef.maxInputs} input connection${targetDef.maxInputs > 1 ? "s" : ""}.`,
      };
    }
  }

  const duplicateEdge = edges.some(
    (edge) =>
      edge.source === source &&
      edge.target === target &&
      (edge.targetHandle ?? "input") === (targetHandle ?? "input"),
  );
  if (duplicateEdge) {
    return { valid: false, message: "This connection already exists." };
  }

  const candidate: BuilderEdge = {
    id: toEdgeId(source, target, targetHandle),
    source,
    sourceHandle: connection.sourceHandle ?? "output",
    target,
    targetHandle,
  };

  if (topologicalSort(nodes, [...edges, candidate]) === null) {
    return { valid: false, message: "This connection would create a cycle." };
  }

  return { valid: true, targetHandle };
}

function uniqueColumns(columns: string[]): string[] {
  return [...new Set(columns.filter((column) => column.trim().length > 0))];
}

export function removeEdgeById(edges: BuilderEdge[], edgeId: string): BuilderEdge[] {
  return edges.filter((edge) => edge.id !== edgeId);
}

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string").map((item) => item.trim()).filter(Boolean)
    : [];
}

export function usePipelineEditor({
  yamlText,
  onYamlTextChange,
  availableFiles,
}: UsePipelineEditorOptions) {
  const [nodes, setNodes, onNodesChangeBase] = useNodesState<BuilderNode>([]);
  const [edges, setEdges, onEdgesChangeBase] = useEdgesState<BuilderEdge>([]);
  const [pipelineName, setPipelineName] = useState("my_pipeline");
  const [configuringNodeId, setConfiguringNodeId] = useState<string | null>(null);
  const [parseError, setParseError] = useState<string | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const parsedFromYamlRef = useRef(false);
  const emittedFromGraphRef = useRef(false);
  const initializedFromYamlRef = useRef(false);
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const yamlTextRef = useRef(yamlText);

  useEffect(() => {
    yamlTextRef.current = yamlText;
  }, [yamlText]);

  const showToast = useCallback((message: string) => {
    setToastMessage(message);
    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }
    toastTimerRef.current = setTimeout(() => setToastMessage(null), 3000);
  }, []);

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  const parseYamlToGraph = useDebouncedCallback((nextYaml: string) => {
    try {
      const parsed = yamlToGraph(nextYaml);
      parsedFromYamlRef.current = true;
      initializedFromYamlRef.current = true;
      setNodes(parsed.nodes);
      setEdges(parsed.edges);
      setPipelineName(parsed.pipelineName);
      setParseError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to parse YAML";
      setParseError(message);
    }
  }, 250);

  const emitGraphToYaml = useDebouncedCallback((graph: BuilderGraph) => {
    try {
      const nextYaml = graphToYAML(graph);
      setParseError(null);
      if (nextYaml !== yamlTextRef.current) {
        emittedFromGraphRef.current = true;
        onYamlTextChange(nextYaml);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to generate YAML";
      setParseError(message);
    }
  }, 250);

  useEffect(() => {
    if (emittedFromGraphRef.current) {
      emittedFromGraphRef.current = false;
      return;
    }
    parseYamlToGraph(yamlText);
  }, [yamlText, parseYamlToGraph]);

  useEffect(() => {
    if (!initializedFromYamlRef.current) {
      return;
    }
    if (parsedFromYamlRef.current) {
      parsedFromYamlRef.current = false;
      return;
    }
    emitGraphToYaml({ pipelineName, nodes, edges });
  }, [edges, emitGraphToYaml, nodes, pipelineName]);

  const selectedNodeId = useMemo(
    () => nodes.find((node) => node.selected)?.id ?? null,
    [nodes],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<BuilderNode>[]) => {
      onNodesChangeBase(changes);
      const removedIds = new Set(
        changes.filter((change) => change.type === "remove").map((change) => change.id),
      );
      if (configuringNodeId && removedIds.has(configuringNodeId)) {
        setConfiguringNodeId(null);
      }
    },
    [configuringNodeId, onNodesChangeBase],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange<BuilderEdge>[]) => {
      onEdgesChangeBase(changes);
    },
    [onEdgesChangeBase],
  );

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      setEdges((prevEdges) => removeEdgeById(prevEdges, edgeId));
    },
    [setEdges],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      const result = validateConnectionCandidate(connection, nodes, edges);
      if (!result.valid || !connection.source || !connection.target) {
        showToast(result.message ?? "Invalid connection.");
        return;
      }

      const edge: BuilderEdge = {
        id: toEdgeId(connection.source, connection.target, result.targetHandle),
        source: connection.source,
        sourceHandle: connection.sourceHandle ?? "output",
        target: connection.target,
        targetHandle: result.targetHandle,
        animated: result.targetHandle === "left" || result.targetHandle === "right",
      };

      setEdges((prevEdges) => addEdge(edge, prevEdges));
    },
    [edges, nodes, setEdges, showToast],
  );

  const handleDragStart = useCallback((event: DragEvent, stepType: VisualStepType) => {
    event.dataTransfer.setData("application/pipeline-step", stepType);
    event.dataTransfer.setData("text/plain", stepType);
    event.dataTransfer.effectAllowed = "move";
  }, []);

  const addStepNode = useCallback(
    (
      stepType: VisualStepType,
      resolvePosition: (existingNodes: BuilderNode[]) => XYPosition,
    ) => {
      if (!isVisualStepType(stepType)) {
        return;
      }

      const definition = STEP_DEFINITIONS[stepType];
      let createdNodeId: string | null = null;

      setNodes((existingNodes) => {
        const siblingCount =
          existingNodes.filter((node) => node.data.type === stepType).length + 1;
        const nodeId = `${stepType}_${Date.now()}_${Math.random()
          .toString(36)
          .slice(2, 8)}`;
        createdNodeId = nodeId;
        const label = `${definition.label}_${siblingCount}`;

        const newNode: BuilderNode = {
          id: nodeId,
          type: "stepNode",
          position: resolvePosition(existingNodes),
          data: {
            label,
            type: stepType,
            config: getDefaultStepConfig(stepType),
            backendSupported: definition.backendSupported,
          },
        };

        return [...existingNodes, newNode];
      });

      if (createdNodeId) {
        setConfiguringNodeId(createdNodeId);
      }
      if (!definition.backendSupported) {
        showToast(`${definition.label} is visual-only and may fail backend validation.`);
      }
    },
    [setNodes, showToast],
  );

  const handleDrop = useCallback(
    (stepType: VisualStepType, position: XYPosition) => {
      addStepNode(stepType, () => position);
    },
    [addStepNode],
  );

  const handleAddStep = useCallback(
    (stepType: VisualStepType) => {
      addStepNode(stepType, (existingNodes) => {
        if (existingNodes.length === 0) {
          return { x: 120, y: 120 };
        }
        const lastNode = existingNodes[existingNodes.length - 1];
        const lastX = Number.isFinite(lastNode.position.x) ? lastNode.position.x : 0;
        const lastY = Number.isFinite(lastNode.position.y) ? lastNode.position.y : 0;
        return { x: lastX + 240, y: lastY };
      });
    },
    [addStepNode],
  );

  const handleConfigure = useCallback((nodeId: string) => {
    setConfiguringNodeId(nodeId);
  }, []);

  const handleConfigClose = useCallback(() => {
    setConfiguringNodeId(null);
  }, []);

  const handleConfigSave = useCallback(
    (nodeId: string, config: Record<string, unknown>) => {
      setNodes((prevNodes) =>
        prevNodes.map((node) =>
          node.id === nodeId
            ? {
                ...node,
                data: {
                  ...node.data,
                  config,
                },
              }
            : node,
        ),
      );
      setConfiguringNodeId(null);
    },
    [setNodes],
  );

  const handleDeleteNode = useCallback(
    (nodeId: string) => {
      setNodes((prevNodes) => prevNodes.filter((node) => node.id !== nodeId));
      setEdges((prevEdges) =>
        prevEdges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
      );
      if (configuringNodeId === nodeId) {
        setConfiguringNodeId(null);
      }
    },
    [configuringNodeId, setEdges, setNodes],
  );

  const handleYamlChange = useCallback(
    (nextYaml: string) => {
      onYamlTextChange(nextYaml);
    },
    [onYamlTextChange],
  );

  const fileById = useMemo(
    () => new Map(availableFiles.map((file) => [file.id, file])),
    [availableFiles],
  );

  const getAvailableColumns = useCallback(
    (nodeId: string): string[] => {
      const nodeById = new Map(nodes.map((node) => [node.id, node]));
      const incomingByTarget = new Map<string, BuilderEdge[]>();
      for (const edge of edges) {
        const list = incomingByTarget.get(edge.target) ?? [];
        list.push(edge);
        incomingByTarget.set(edge.target, list);
      }

      const outputCache = new Map<string, string[]>();

      const inferOutputColumns = (id: string, visiting: Set<string>): string[] => {
        const cached = outputCache.get(id);
        if (cached) {
          return cached;
        }
        if (visiting.has(id)) {
          return [];
        }
        visiting.add(id);

        const node = nodeById.get(id);
        if (!node) {
          visiting.delete(id);
          return [];
        }

        const incoming = incomingByTarget.get(id) ?? [];
        const parentColumns = uniqueColumns(
          incoming.flatMap((edge) => inferOutputColumns(edge.source, visiting)),
        );

        let outputColumns = parentColumns;
        switch (node.data.type) {
          case "load": {
            const fileId = typeof node.data.config.file_id === "string" ? node.data.config.file_id : "";
            const fileColumns = fileById.get(fileId)?.columns ?? [];
            outputColumns = uniqueColumns(fileColumns);
            break;
          }
          case "select": {
            const columns = asStringArray(node.data.config.columns);
            outputColumns = columns.length > 0 ? columns : parentColumns;
            break;
          }
          case "rename": {
            const mapping = node.data.config.mapping;
            const record = mapping && typeof mapping === "object" ? (mapping as Record<string, unknown>) : {};
            outputColumns = parentColumns.map((column) => {
              const next = record[column];
              return typeof next === "string" && next.trim() ? next.trim() : column;
            });
            break;
          }
          case "aggregate": {
            const groupBy = asStringArray(node.data.config.group_by);
            const aggregationsRaw = Array.isArray(node.data.config.aggregations)
              ? node.data.config.aggregations
              : [];
            const aggColumns = aggregationsRaw
              .map((agg) => {
                const item = agg && typeof agg === "object" ? (agg as Record<string, unknown>) : {};
                const column = asString(item.column);
                const fn = asString(item.function);
                return column && fn ? `${column}_${fn}` : "";
              })
              .filter((column) => column.length > 0);
            outputColumns = uniqueColumns([...groupBy, ...aggColumns]);
            break;
          }
          case "join": {
            const leftEdge = incoming.find((edge) => edge.targetHandle === "left") ?? incoming[0];
            const rightEdge =
              incoming.find((edge) => edge.targetHandle === "right") ??
              incoming.find((edge) => edge !== leftEdge);

            const leftCols = leftEdge ? inferOutputColumns(leftEdge.source, visiting) : [];
            const rightCols = rightEdge ? inferOutputColumns(rightEdge.source, visiting) : [];

            const leftSet = new Set(leftCols);
            outputColumns = uniqueColumns([
              ...leftCols,
              ...rightCols.map((column) => (leftSet.has(column) ? `${column}_right` : column)),
            ]);
            break;
          }
          case "pivot": {
            const index = asStringArray(node.data.config.index);
            const values = asString(node.data.config.values);
            const columns = asString(node.data.config.columns);
            const derived = values && columns ? `${values}_${columns}` : "";
            outputColumns = uniqueColumns([...index, derived]);
            break;
          }
          case "unpivot": {
            const idVars = asStringArray(node.data.config.id_vars);
            const varName = asString(node.data.config.var_name) || "variable";
            const valueName = asString(node.data.config.value_name) || "value";
            outputColumns = uniqueColumns([...idVars, varName, valueName]);
            break;
          }
          default:
            outputColumns = parentColumns;
        }

        outputCache.set(id, outputColumns);
        visiting.delete(id);
        return outputColumns;
      };

      const node = nodeById.get(nodeId);
      if (!node) {
        return [];
      }

      const incoming = incomingByTarget.get(nodeId) ?? [];
      if (node.data.type === "join") {
        const leftEdge = incoming.find((edge) => edge.targetHandle === "left") ?? incoming[0];
        const rightEdge =
          incoming.find((edge) => edge.targetHandle === "right") ??
          incoming.find((edge) => edge !== leftEdge);
        const leftCols = leftEdge ? inferOutputColumns(leftEdge.source, new Set()) : [];
        const rightCols = rightEdge ? inferOutputColumns(rightEdge.source, new Set()) : [];
        if (leftCols.length > 0 && rightCols.length > 0) {
          const rightSet = new Set(rightCols);
          return leftCols.filter((column) => rightSet.has(column));
        }
        return uniqueColumns([...leftCols, ...rightCols]);
      }

      const parent = incoming[0];
      if (!parent) {
        return [];
      }
      return inferOutputColumns(parent.source, new Set());
    },
    [edges, fileById, nodes],
  );

  return {
    yamlText,
    pipelineName,
    setPipelineName,
    nodes,
    edges,
    selectedNodeId,
    configuringNodeId,
    parseError,
    toastMessage,
    onNodesChange,
    onEdgesChange,
    handleConnect,
    handleDragStart,
    handleDrop,
    handleAddStep,
    handleConfigure,
    handleConfigClose,
    handleConfigSave,
    handleDeleteNode,
    handleDeleteEdge,
    handleYamlChange,
    getAvailableColumns,
  };
}
