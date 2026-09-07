from __future__ import annotations

import pytest

from tests.factories import NOW, branching_workflow, linear_workflow, make_edge, make_node
from workflow_engine.domain import (
    DuplicateNodeError,
    InvalidEdgeError,
    InvalidWorkflowError,
    NodeType,
    WorkflowDefinition,
    WorkflowNode,
)
from workflow_engine.domain.condition import ConditionLeaf


def test_valid_linear_workflow_constructs_successfully() -> None:
    wf = linear_workflow()
    assert wf.start_node_id == "start"
    assert len(wf.nodes) == 3
    assert wf.checksum  # computed, non-empty


def test_valid_branching_workflow_constructs_successfully() -> None:
    wf = branching_workflow()
    assert len(wf.edges) == 4


def test_checksum_is_deterministic_and_order_independent() -> None:
    wf_a = linear_workflow()
    nodes = list(reversed(wf_a.nodes))
    edges = list(reversed(wf_a.edges))
    wf_b = WorkflowDefinition(
        id=wf_a.id,
        version=wf_a.version,
        name=wf_a.name,
        nodes=nodes,
        edges=edges,
        start_node_id=wf_a.start_node_id,
        created_at=NOW,
    )
    assert wf_a.checksum == wf_b.checksum


def test_checksum_changes_when_graph_changes() -> None:
    wf_a = linear_workflow()
    wf_b = linear_workflow()
    # perturb an edge id, which changes graph shape without breaking validity
    edges = [make_edge("e1-renamed", "start", "transform"), wf_b.edges[1]]
    wf_c = WorkflowDefinition(
        id=wf_b.id,
        version=wf_b.version,
        name=wf_b.name,
        nodes=wf_b.nodes,
        edges=edges,
        start_node_id=wf_b.start_node_id,
        created_at=NOW,
    )
    assert wf_a.checksum != wf_c.checksum


def test_duplicate_node_id_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("start", NodeType.END),
    ]
    edges = [make_edge("e1", "start", "start")]
    with pytest.raises(DuplicateNodeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="dup",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_edge_referencing_missing_node_rejected() -> None:
    nodes = [make_node("start", NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "start", "ghost")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="missing",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_duplicate_edge_id_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("classify", NodeType.CONDITION),
        make_node("a", NodeType.END),
        make_node("b", NodeType.END),
    ]
    cond = ConditionLeaf(field="x", operator="eq", value=1)
    edges = [
        make_edge("e1", "start", "classify"),
        make_edge("e1", "classify", "a", condition=cond),
        make_edge("e2", "classify", "b"),
    ]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="dup-edge",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_edge_source_referencing_missing_node_rejected() -> None:
    nodes = [make_node("start", NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "start", "end"), make_edge("e2", "ghost", "end")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="missing-source",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_edge_target_referencing_missing_node_rejected() -> None:
    nodes = [make_node("start", NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "start", "end"), make_edge("e2", "end", "ghost")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="missing-target",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_start_node_id_referencing_unknown_node_rejected() -> None:
    nodes = [make_node("start", NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "start", "end")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="ghost-start-ref",
            nodes=nodes,
            edges=edges,
            start_node_id="ghost",
            created_at=NOW,
        )


def test_diamond_reachability_visits_shared_node_once() -> None:
    """start -> {a, b} -> merge -> end, via a CONDITION node fan-out, exercises
    the BFS "already visited" branch in end-reachability checking (`merge` is
    reached from both `a` and `b`)."""
    nodes = [
        make_node("start", NodeType.START),
        make_node("classify", NodeType.CONDITION),
        make_node("a", NodeType.TRANSFORM),
        make_node("b", NodeType.TRANSFORM),
        make_node("merge", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    cond = ConditionLeaf(field="x", operator="eq", value=1)
    edges = [
        make_edge("e1", "start", "classify"),
        make_edge("e2", "classify", "a", condition=cond),
        make_edge("e3", "classify", "b"),
        make_edge("e4", "a", "merge"),
        make_edge("e5", "b", "merge"),
        make_edge("e6", "merge", "end"),
    ]
    wf = WorkflowDefinition(
        id="wf",
        version=1,
        name="diamond",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    assert wf.checksum


def test_start_node_with_incoming_edge_rejected() -> None:
    """DESIGN.md §4.1 rule 8 (Fase 2): nothing may precede START. Isolated from
    the acyclic check: `a` has exactly one outgoing edge (satisfies the
    implicit-branching rule), so this fails specifically on the START-incoming
    rule, not on cycle detection (both would independently reject this graph
    since start->a->start is also a cycle, but the point is to prove the
    dedicated rule fires)."""
    nodes = [make_node("start", NodeType.START), make_node("a", NodeType.TRANSFORM)]
    edges = [make_edge("e1", "start", "a"), make_edge("e2", "a", "start")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="start-incoming",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_end_node_with_outgoing_edge_rejected() -> None:
    """DESIGN.md §4.1 rule 9 (Fase 2 gap fix): END must be a true terminal node."""
    nodes = [
        make_node("start", NodeType.START),
        make_node("end", NodeType.END),
        make_node("a", NodeType.TRANSFORM),
    ]
    edges = [make_edge("e1", "start", "end"), make_edge("e2", "end", "a")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="end-outgoing",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_multiple_end_nodes_allowed() -> None:
    """DESIGN.md doesn't cap the number of END nodes — a branching workflow can
    terminate in more than one place, as long as each END is reachable and has
    no outgoing edges (branching_workflow fixture already exercises this)."""
    wf = branching_workflow()
    end_nodes = [n for n in wf.nodes if n.type == NodeType.END]
    assert len(end_nodes) == 2


def test_self_loop_rejected() -> None:
    """A -> A is a cycle of length 1; must be rejected by the same acyclic
    check as any longer cycle."""
    nodes = [make_node("start", NodeType.START), make_node("a", NodeType.TRANSFORM)]
    edges = [make_edge("e1", "start", "a"), make_edge("e2", "a", "a")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="self-loop",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_duplicate_edge_pair_with_different_ids_rejected() -> None:
    """A -> B declared twice (different edge ids, same source/target) is
    ambiguous and must be rejected, not silently deduplicated."""
    nodes = [
        make_node("start", NodeType.START),
        make_node("a", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    edges = [
        make_edge("e1", "start", "a"),
        make_edge("e2", "a", "end"),
        make_edge("e3", "a", "end"),  # duplicate pair, distinct id
    ]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="dup-pair",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_missing_start_node_rejected() -> None:
    nodes = [make_node("t", NodeType.TRANSFORM), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "t", "end")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="no-start",
            nodes=nodes,
            edges=edges,
            start_node_id="t",
            created_at=NOW,
        )


def test_multiple_start_nodes_rejected() -> None:
    nodes = [
        make_node("start1", NodeType.START),
        make_node("start2", NodeType.START),
        make_node("end", NodeType.END),
    ]
    edges = [make_edge("e1", "start1", "end"), make_edge("e2", "start2", "end")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="two-starts",
            nodes=nodes,
            edges=edges,
            start_node_id="start1",
            created_at=NOW,
        )


def test_start_node_id_pointing_to_non_start_node_rejected() -> None:
    nodes = [make_node("start", NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", "start", "end")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="wrong-start-ref",
            nodes=nodes,
            edges=edges,
            start_node_id="end",
            created_at=NOW,
        )


def test_no_end_reachable_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("t", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    # end exists but is unreachable from start
    edges = [make_edge("e1", "start", "t")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="unreachable-end",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_cycle_rejected() -> None:
    """A cycle can only exist behind a CONDITION node in V1, since every other
    node is limited to a single outgoing edge (see
    test_non_condition_node_with_two_outgoing_edges_rejected). This graph is
    otherwise fully valid — no orphans, END reachable, single-out-edge rule
    respected everywhere except the CONDITION node — so it isolates the
    cycle check itself: start -> a -> b -> classify -> {loop back to a, end}.
    """
    nodes = [
        make_node("start", NodeType.START),
        make_node("a", NodeType.TRANSFORM),
        make_node("b", NodeType.TRANSFORM),
        make_node("classify", NodeType.CONDITION),
        make_node("end", NodeType.END),
    ]
    loop_condition = ConditionLeaf(field="retry", operator="eq", value=True)
    edges = [
        make_edge("e1", "start", "a"),
        make_edge("e2", "a", "b"),
        make_edge("e3", "b", "classify"),
        make_edge("e4", "classify", "a", condition=loop_condition),  # closes the cycle
        make_edge("e5", "classify", "end"),  # default branch
    ]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="cycle",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_orphan_node_with_no_incoming_edge_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("orphan", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    edges = [make_edge("e1", "start", "end"), make_edge("e2", "orphan", "end")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="orphan-in",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_dead_end_node_with_no_outgoing_edge_rejected() -> None:
    """A TRANSFORM node with no outgoing edge at all — isolates the dead-end
    check from the (separate) implicit-branching check."""
    nodes = [make_node("start", NodeType.START), make_node("dead", NodeType.TRANSFORM)]
    edges = [make_edge("e1", "start", "dead")]
    with pytest.raises(InvalidWorkflowError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="dead-end",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_condition_node_with_single_outgoing_edge_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("classify", NodeType.CONDITION),
        make_node("end", NodeType.END),
    ]
    edges = [make_edge("e1", "start", "classify"), make_edge("e2", "classify", "end")]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="single-branch",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_condition_node_with_two_default_edges_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("classify", NodeType.CONDITION),
        make_node("a", NodeType.END),
        make_node("b", NodeType.END),
    ]
    edges = [
        make_edge("e1", "start", "classify"),
        make_edge("e2", "classify", "a"),  # no condition
        make_edge("e3", "classify", "b"),  # no condition — two defaults, ambiguous
    ]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="two-defaults",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_non_condition_node_with_two_outgoing_edges_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("t", NodeType.TRANSFORM),
        make_node("a", NodeType.END),
        make_node("b", NodeType.END),
    ]
    edges = [
        make_edge("e1", "start", "t"),
        make_edge("e2", "t", "a"),
        make_edge("e3", "t", "b"),
    ]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="implicit-branch",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_non_condition_node_with_conditioned_edge_rejected() -> None:
    nodes = [
        make_node("start", NodeType.START),
        make_node("t", NodeType.TRANSFORM),
        make_node("end", NodeType.END),
    ]
    cond = ConditionLeaf(field="x", operator="eq", value=1)
    edges = [
        make_edge("e1", "start", "t"),
        make_edge("e2", "t", "end", condition=cond),
    ]
    with pytest.raises(InvalidEdgeError):
        WorkflowDefinition(
            id="wf",
            version=1,
            name="conditioned-non-branch",
            nodes=nodes,
            edges=edges,
            start_node_id="start",
            created_at=NOW,
        )


def test_workflow_version_must_be_positive() -> None:
    with pytest.raises(InvalidWorkflowError):
        linear_workflow(version=0)


def test_unknown_node_type_rejected() -> None:
    """NodeType is a closed enum — an unrecognized type string must fail
    Pydantic validation before the graph validator ever runs."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkflowNode(id="n1", type="NOT_A_REAL_TYPE")  # type: ignore[arg-type]


def test_malicious_node_and_workflow_ids_are_inert_data() -> None:
    """A node/workflow id containing a code-injection-shaped string is treated
    as an opaque identifier — never interpreted, executed, or used to build a
    file path/query."""
    payload = "'; DROP TABLE workflows; -- ../../etc/passwd ${jndi:ldap://x}"
    nodes = [make_node(payload, NodeType.START), make_node("end", NodeType.END)]
    edges = [make_edge("e1", payload, "end")]
    wf = WorkflowDefinition(
        id=payload,
        version=1,
        name="malicious-ids",
        nodes=nodes,
        edges=edges,
        start_node_id=payload,
        created_at=NOW,
    )
    assert wf.id == payload
    assert wf.nodes[0].id == payload


def test_oversized_definition_is_just_validated_slower_not_unsafe() -> None:
    """A large-but-linear workflow (many TRANSFORM nodes in a chain) must still
    validate correctly — no quadratic blow-up, no crash, no special-casing
    needed for size."""
    n = 500
    nodes = [make_node("start", NodeType.START)]
    edges = []
    prev = "start"
    for i in range(n):
        node_id = f"t{i}"
        nodes.append(make_node(node_id, NodeType.TRANSFORM))
        edges.append(make_edge(f"e{i}", prev, node_id))
        prev = node_id
    nodes.append(make_node("end", NodeType.END))
    edges.append(make_edge("e-final", prev, "end"))

    wf = WorkflowDefinition(
        id="wf-large",
        version=1,
        name="large",
        nodes=nodes,
        edges=edges,
        start_node_id="start",
        created_at=NOW,
    )
    assert len(wf.nodes) == n + 2


def test_deeply_nested_config_does_not_execute_or_crash() -> None:
    """A WorkflowNode.config with deeply nested metadata is just data — it
    round-trips through validation without being interpreted."""
    nested: dict[str, object] = {"value": 1}
    for _ in range(50):
        nested = {"child": nested}
    node = make_node("n1", NodeType.TRANSFORM, config=nested)
    assert node.config["child"]  # just confirms it's stored as inert data
