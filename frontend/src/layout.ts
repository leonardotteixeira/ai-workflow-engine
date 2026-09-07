import type { WorkflowEdge, WorkflowNode } from "./types";

export interface PositionedNode {
  id: string;
  x: number;
  y: number;
}

/** Simple leveled (BFS-depth) layout — good enough for the small DAGs this
 * project produces. A real layout library (e.g. dagre) would be reasonable
 * for arbitrary graphs, but pulling one in for workflows with a handful of
 * nodes would be the "artificial complexity" the brief explicitly warns
 * against. */
export function layoutNodes(nodes: WorkflowNode[], edges: WorkflowEdge[]): PositionedNode[] {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    const list = outgoing.get(edge.source_node_id) ?? [];
    list.push(edge.target_node_id);
    outgoing.set(edge.source_node_id, list);
  }

  const start = nodes.find((n) => n.type === "START");
  const depths = new Map<string, number>();
  if (start) {
    const queue: [string, number][] = [[start.id, 0]];
    while (queue.length > 0) {
      const [id, depth] = queue.shift()!;
      if (depths.has(id) && depths.get(id)! <= depth) continue;
      depths.set(id, depth);
      for (const next of outgoing.get(id) ?? []) queue.push([next, depth + 1]);
    }
  }

  const perLevelCount = new Map<number, number>();
  return nodes.map((node) => {
    const depth = depths.get(node.id) ?? 0;
    const indexInLevel = perLevelCount.get(depth) ?? 0;
    perLevelCount.set(depth, indexInLevel + 1);
    return { id: node.id, x: depth * 220, y: indexInLevel * 120 };
  });
}
