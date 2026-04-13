import type { Edge, Node } from "@xyflow/react";

export function topologicalSort<TNode extends Node>(
  nodes: TNode[],
  edges: Edge[],
): TNode[] | null {
  if (nodes.length === 0) {
    return [];
  }

  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const nodeIds = nodes.map((node) => node.id);
  const adjacency = new Map<string, Set<string>>();
  const inDegree = new Map<string, number>();

  for (const id of nodeIds) {
    adjacency.set(id, new Set());
    inDegree.set(id, 0);
  }

  for (const edge of edges) {
    if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) {
      continue;
    }
    const neighbors = adjacency.get(edge.source);
    if (!neighbors || neighbors.has(edge.target)) {
      continue;
    }
    neighbors.add(edge.target);
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
  }

  const queue = [...nodeIds].filter((id) => (inDegree.get(id) ?? 0) === 0).sort();
  const order: string[] = [];

  while (queue.length > 0) {
    const id = queue.shift();
    if (!id) {
      break;
    }
    order.push(id);

    for (const neighbor of adjacency.get(id) ?? []) {
      const next = (inDegree.get(neighbor) ?? 0) - 1;
      inDegree.set(neighbor, next);
      if (next === 0) {
        queue.push(neighbor);
        queue.sort();
      }
    }
  }

  if (order.length !== nodeIds.length) {
    return null;
  }

  return order.map((id) => nodeById.get(id)).filter((node): node is TNode => Boolean(node));
}

export function hasCycle<TNode extends Node>(nodes: TNode[], edges: Edge[]): boolean {
  return topologicalSort(nodes, edges) === null;
}
