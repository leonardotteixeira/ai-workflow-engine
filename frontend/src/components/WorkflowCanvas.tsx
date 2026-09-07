import { useMemo } from "react";
import ReactFlow, { Background, type Edge, type Node, Position } from "reactflow";
import "reactflow/dist/style.css";
import { layoutNodes } from "../layout";
import type { WorkflowEdge, WorkflowNode } from "../types";

interface Props {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  nodeStatuses: Record<string, string>;
}

function NodeLabel({ node, status }: { node: WorkflowNode; status?: string }) {
  return (
    <div className={`wf-node${status ? ` status-${status}` : ""}`}>
      <div className="node-type">{node.type.replace("_", " ")}</div>
      <div>{node.id}</div>
    </div>
  );
}

export function WorkflowCanvas({ nodes, edges, nodeStatuses }: Props) {
  const positions = useMemo(() => layoutNodes(nodes, edges), [nodes, edges]);

  const flowNodes: Node[] = useMemo(
    () =>
      nodes.map((node) => {
        const pos = positions.find((p) => p.id === node.id) ?? { x: 0, y: 0 };
        return {
          id: node.id,
          position: { x: pos.x, y: pos.y },
          data: { label: <NodeLabel node={node} status={nodeStatuses[node.id]} /> },
          sourcePosition: Position.Right,
          targetPosition: Position.Left,
          style: { background: "transparent", border: "none", padding: 0 },
        };
      }),
    [nodes, positions, nodeStatuses],
  );

  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((edge) => ({
        id: edge.id,
        source: edge.source_node_id,
        target: edge.target_node_id,
        animated: edge.condition != null,
        label: edge.condition
          ? `${(edge.condition as { field?: string }).field ?? ""}`
          : undefined,
      })),
    [edges],
  );

  if (nodes.length === 0) {
    return <div className="empty-state">No workflow loaded yet.</div>;
  }

  return (
    <ReactFlow
      nodes={flowNodes}
      edges={flowEdges}
      fitView
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={16} />
    </ReactFlow>
  );
}
