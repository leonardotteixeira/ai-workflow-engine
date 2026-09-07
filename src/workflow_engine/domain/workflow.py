"""WorkflowDefinition / WorkflowNode / WorkflowEdge — DESIGN.md §2.1, §4.

Definitions are immutable once constructed: validation runs at construction
time (`validate_graph`) so an invalid graph can never exist as a
`WorkflowDefinition` instance. V1 is DAG-only — cycles are rejected, not
worked around (DESIGN.md §4, "Workflow Graph").
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from workflow_engine.domain.condition import ConditionExpr
from workflow_engine.domain.enums import NodeType
from workflow_engine.domain.errors import DuplicateNodeError, InvalidEdgeError, InvalidWorkflowError
from workflow_engine.domain.policies import RetryPolicy, TimeoutPolicy


class WorkflowNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    type: NodeType
    config: dict[str, object] = {}
    retry_policy: RetryPolicy | None = None
    timeout_policy: TimeoutPolicy | None = None


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    source_node_id: str
    target_node_id: str
    condition: ConditionExpr | None = None


def validate_graph(
    nodes: list[WorkflowNode], edges: list[WorkflowEdge], start_node_id: str
) -> None:
    """Structural validation of a workflow graph — DESIGN.md §4.1, rules 1-7.

    Raises DuplicateNodeError / InvalidEdgeError / InvalidWorkflowError on the
    first violation found. Pure function: no I/O, only in-memory graph checks.
    """
    _check_duplicate_node_ids(nodes)
    node_by_id = {n.id: n for n in nodes}

    _check_duplicate_edge_ids(edges)
    _check_duplicate_edge_pairs(edges)
    _check_edge_references(node_by_id, edges)

    _check_start_node(node_by_id, start_node_id)
    _check_condition_node_edges(node_by_id, edges)
    _check_no_implicit_branching(node_by_id, edges)

    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in edges:
        outgoing[edge.source_node_id].append(edge)
        incoming[edge.target_node_id].append(edge)

    _check_start_has_no_incoming(node_by_id, incoming, start_node_id)
    _check_end_has_no_outgoing(node_by_id, outgoing)
    _check_no_orphans(node_by_id, outgoing, incoming, start_node_id)
    _check_acyclic(node_by_id, outgoing)
    _check_end_reachable(node_by_id, outgoing, start_node_id)


def _check_duplicate_node_ids(nodes: list[WorkflowNode]) -> None:
    seen: set[str] = set()
    for node in nodes:
        if node.id in seen:
            raise DuplicateNodeError(f"duplicate node id: {node.id!r}")
        seen.add(node.id)


def _check_duplicate_edge_ids(edges: list[WorkflowEdge]) -> None:
    seen: set[str] = set()
    for edge in edges:
        if edge.id in seen:
            raise InvalidEdgeError(f"duplicate edge id: {edge.id!r}")
        seen.add(edge.id)


def _check_duplicate_edge_pairs(edges: list[WorkflowEdge]) -> None:
    """Reject two edges with the same (source, target) pair — even with
    different edge ids. Fase 2 gap: a graph author could otherwise express
    "A -> B" twice, which is ambiguous (which one does the Engine follow?)
    and must be rejected explicitly rather than silently deduplicated.
    """
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        pair = (edge.source_node_id, edge.target_node_id)
        if pair in seen:
            raise InvalidEdgeError(
                f"duplicate edge: {edge.source_node_id!r} -> {edge.target_node_id!r} "
                "is declared more than once"
            )
        seen.add(pair)


def _check_edge_references(node_by_id: dict[str, WorkflowNode], edges: list[WorkflowEdge]) -> None:
    for edge in edges:
        if edge.source_node_id not in node_by_id:
            raise InvalidEdgeError(
                f"edge {edge.id!r} references unknown source node {edge.source_node_id!r}"
            )
        if edge.target_node_id not in node_by_id:
            raise InvalidEdgeError(
                f"edge {edge.id!r} references unknown target node {edge.target_node_id!r}"
            )


def _check_start_node(node_by_id: dict[str, WorkflowNode], start_node_id: str) -> None:
    start_nodes = [n for n in node_by_id.values() if n.type == NodeType.START]
    if len(start_nodes) != 1:
        raise InvalidWorkflowError(
            f"workflow must have exactly one START node, found {len(start_nodes)}"
        )
    if start_node_id not in node_by_id:
        raise InvalidWorkflowError(f"start_node_id {start_node_id!r} is not a known node")
    if node_by_id[start_node_id].type != NodeType.START:
        raise InvalidWorkflowError(
            f"start_node_id {start_node_id!r} does not reference a START node"
        )


def _check_start_has_no_incoming(
    node_by_id: dict[str, WorkflowNode],
    incoming: dict[str, list[WorkflowEdge]],
    start_node_id: str,
) -> None:
    """Fase 2 gap: START must have zero incoming edges — nothing can precede
    the entry point of a workflow."""
    if incoming.get(start_node_id):
        raise InvalidEdgeError(f"START node {start_node_id!r} cannot have incoming edges")


def _check_end_has_no_outgoing(
    node_by_id: dict[str, WorkflowNode], outgoing: dict[str, list[WorkflowEdge]]
) -> None:
    """Fase 2 gap identified during the Fase 1 -> Fase 2 transition: DESIGN.md
    §4.1 never said END must have zero outgoing edges, only that non-END nodes
    need >=1 outgoing. Made explicit here per the Fase 2 brief."""
    for node in node_by_id.values():
        if node.type == NodeType.END and outgoing.get(node.id):
            raise InvalidEdgeError(f"END node {node.id!r} cannot have outgoing edges")


def _check_condition_node_edges(
    node_by_id: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> None:
    by_source: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_node_id].append(edge)

    for node in node_by_id.values():
        if node.type != NodeType.CONDITION:
            continue
        out_edges = by_source.get(node.id, [])
        if len(out_edges) < 2:
            raise InvalidEdgeError(
                f"CONDITION node {node.id!r} must have at least 2 outgoing edges"
            )
        unconditioned = [e for e in out_edges if e.condition is None]
        if len(unconditioned) > 1:
            raise InvalidEdgeError(
                f"CONDITION node {node.id!r} may have at most one default "
                "(unconditioned) outgoing edge"
            )


def _check_no_implicit_branching(
    node_by_id: dict[str, WorkflowNode], edges: list[WorkflowEdge]
) -> None:
    by_source: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_node_id].append(edge)

    for node in node_by_id.values():
        if node.type == NodeType.CONDITION:
            continue
        out_edges = by_source.get(node.id, [])
        if len(out_edges) > 1:
            raise InvalidEdgeError(
                f"non-CONDITION node {node.id!r} must have at most 1 outgoing edge"
            )
        if out_edges and out_edges[0].condition is not None:
            raise InvalidEdgeError(
                f"non-CONDITION node {node.id!r} cannot have a conditioned outgoing edge"
            )


def _check_no_orphans(
    node_by_id: dict[str, WorkflowNode],
    outgoing: dict[str, list[WorkflowEdge]],
    incoming: dict[str, list[WorkflowEdge]],
    start_node_id: str,
) -> None:
    for node in node_by_id.values():
        if node.id != start_node_id and not incoming.get(node.id):
            raise InvalidWorkflowError(f"node {node.id!r} has no incoming edge (orphan)")
        if node.type != NodeType.END and not outgoing.get(node.id):
            raise InvalidWorkflowError(f"node {node.id!r} has no outgoing edge (dead end)")


def _check_acyclic(
    node_by_id: dict[str, WorkflowNode], outgoing: dict[str, list[WorkflowEdge]]
) -> None:
    """Kahn's algorithm: if not every node can be topologically ordered, a cycle exists."""
    in_degree = {node_id: 0 for node_id in node_by_id}
    for edges in outgoing.values():
        for edge in edges:
            in_degree[edge.target_node_id] += 1

    queue = deque(node_id for node_id, degree in in_degree.items() if degree == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for edge in outgoing.get(current, []):
            in_degree[edge.target_node_id] -= 1
            if in_degree[edge.target_node_id] == 0:
                queue.append(edge.target_node_id)

    if visited != len(node_by_id):
        raise InvalidWorkflowError("workflow graph contains a cycle; V1 supports DAGs only")


def _check_end_reachable(
    node_by_id: dict[str, WorkflowNode], outgoing: dict[str, list[WorkflowEdge]], start_node_id: str
) -> None:
    visited: set[str] = set()
    queue = deque([start_node_id])
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for edge in outgoing.get(current, []):
            queue.append(edge.target_node_id)

    if not any(node_by_id[node_id].type == NodeType.END for node_id in visited):
        raise InvalidWorkflowError("no END node is reachable from the START node")


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    version: int
    name: str
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]
    start_node_id: str
    created_at: datetime

    @model_validator(mode="after")
    def _validate(self) -> WorkflowDefinition:
        if self.version < 1:
            raise InvalidWorkflowError("WorkflowDefinition.version must be >= 1")
        validate_graph(self.nodes, self.edges, self.start_node_id)
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def checksum(self) -> str:
        """Deterministic hash of the graph shape — used to detect drift on replay
        (DESIGN.md §2.1). Computed, not stored: it is fully determined by nodes/
        edges/start_node_id, so persisting it separately would risk it going stale.
        """
        payload = {
            "nodes": [n.model_dump(mode="json") for n in sorted(self.nodes, key=lambda n: n.id)],
            "edges": [e.model_dump(mode="json") for e in sorted(self.edges, key=lambda e: e.id)],
            "start_node_id": self.start_node_id,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
