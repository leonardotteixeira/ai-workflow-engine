"""Next-node resolution — DESIGN.md §6, Fase 3 brief §3.6.

Kept out of the Engine's main loop (engine.py) so branch evaluation stays
independently testable and the Engine itself never inlines condition logic —
it only ever calls `evaluate()` from the Fase 1 condition DSL (data-only,
no eval/exec) through this module.
"""

from __future__ import annotations

from collections import defaultdict

from workflow_engine.domain import ExecutionContext, WorkflowEdge, WorkflowNode
from workflow_engine.domain.condition import evaluate
from workflow_engine.domain.enums import NodeType
from workflow_engine.engine.errors import NoMatchingBranchError


def build_edges_by_source(edges: list[WorkflowEdge]) -> dict[str, list[WorkflowEdge]]:
    by_source: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in edges:
        by_source[edge.source_node_id].append(edge)
    return by_source


def resolve_next_node_id(
    node: WorkflowNode,
    edges_by_source: dict[str, list[WorkflowEdge]],
    context: ExecutionContext,
) -> str | None:
    """Return the id of the node to run after `node`, or None if `node` has no
    outgoing edges (i.e. it is an END node — graph validation guarantees only
    END nodes lack outgoing edges, DESIGN.md §4.1 rule 9).

    For non-CONDITION nodes there is exactly one outgoing edge (graph
    validation rule 6) — take it unconditionally. For CONDITION nodes,
    evaluate each conditioned edge in declaration order and take the first
    match; fall back to the (at most one) default edge; raise
    NoMatchingBranchError if neither exists.
    """
    out_edges = edges_by_source.get(node.id, [])
    if not out_edges:
        return None

    if node.type != NodeType.CONDITION:
        return out_edges[0].target_node_id

    default_edge: WorkflowEdge | None = None
    for edge in out_edges:
        if edge.condition is None:
            default_edge = edge
            continue
        if evaluate(edge.condition, context.variables):
            return edge.target_node_id

    if default_edge is not None:
        return default_edge.target_node_id

    raise NoMatchingBranchError(
        f"CONDITION node {node.id!r}: no edge condition matched and no default edge exists"
    )
