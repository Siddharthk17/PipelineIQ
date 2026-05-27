import { Node, Edge } from '@xyflow/react'

export interface SerializedNode {
  id: string
  type: string
  position: { x: number; y: number }
  data: {
    type: string
    name: string
    config: Record<string, unknown>
    inferredSchema?: string[]
    validationError?: string
  }
  selected?: boolean
  width?: number
  height?: number
}

export interface SerializedEdge {
  id: string
  source: string
  target: string
  sourceHandle?: string
  targetHandle?: string
  animated?: boolean
}

export function serializeNode(node: Node): SerializedNode {
  return {
    id: node.id,
    type: node.type || 'stepNode',
    position: { x: node.position.x, y: node.position.y },
    data: {
      type: (node.data as Record<string, unknown>)?.type as string || '',
      name: (node.data as Record<string, unknown>)?.name as string || node.id,
      config: ((node.data as Record<string, unknown>)?.config as Record<string, unknown>) || {},
      inferredSchema: (node.data as Record<string, unknown>)?.inferredSchema as string[] | undefined,
      validationError: (node.data as Record<string, unknown>)?.validationError as string | undefined,
    },
    selected: node.selected,
    width: node.width,
    height: node.height,
  }
}

export function deserializeNode(
  serialized: SerializedNode,
  onConfigure: (nodeId: string) => void,
): Node {
  return {
    id: serialized.id,
    type: serialized.type,
    position: serialized.position,
    data: {
      ...serialized.data,
      onConfigure,
    },
    selected: serialized.selected,
    width: serialized.width,
    height: serialized.height,
  }
}

export function serializeEdge(edge: Edge): SerializedEdge {
  return {
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle ?? undefined,
    targetHandle: edge.targetHandle ?? undefined,
    animated: edge.animated,
  }
}

export function deserializeEdge(serialized: SerializedEdge): Edge {
  return {
    ...serialized,
    style: { stroke: '#60a5fa', strokeWidth: 1.5 },
  }
}

const COLLABORATOR_COLORS = [
  '#EF4444',
  '#F97316',
  '#EAB308',
  '#22C55E',
  '#3B82F6',
  '#8B5CF6',
  '#EC4899',
  '#06B6D4',
]

export function getColorForUser(userId: string): string {
  let hash = 0
  for (let i = 0; i < userId.length; i++) {
    hash = userId.charCodeAt(i) + ((hash << 5) - hash)
  }
  return COLLABORATOR_COLORS[Math.abs(hash) % COLLABORATOR_COLORS.length]
}
