"""ExecutionEngine — Fase 3.

Orchestrates a single `Execution` through a `WorkflowDefinition`'s graph,
node by node, until it reaches COMPLETED, FAILED, or WAITING. It is
in-memory only: no persistence, no event store, no recovery. The caller gets
back the final `Execution` plus the full in-memory history of this run
(`NodeExecution` records and `Event`s) and decides what to do with them.

The Engine is deliberately agnostic of everything below it:
- it never imports FastAPI, SQLAlchemy, httpx, OpenAI, or any I/O library;
- it never branches on `node.type` — it asks `NodeExecutorRegistry` which
  executor handles a given `NodeType` (DESIGN.md §6.2);
- it never evaluates a condition expression itself — it delegates to
  `next_node.resolve_next_node_id`, which in turn only calls the vetted,
  eval-free `condition.evaluate()` from the Fase 1 domain layer.

What this phase deliberately does NOT do (see DESIGN.md roadmap):
- no persistence/durability/recovery — the Engine has no idea a process
  might crash mid-run; that's the Fase 7 concern.

Fase 8 adds retry: a node that returns `status="failed"` whose `WorkflowNode`
declares a `RetryPolicy` matching the error's category is retried
*synchronously, within the same loop* — up to `max_attempts`, using
`retry.compute_backoff_delay` to compute (and record on the FAILED
NodeExecution as `next_retry_at`, for observability/audit) what a real
scheduler-backed wait would have been. It does NOT actually sleep: V1 has no
background scheduler (explicitly out of scope — DESIGN.md roadmap), so
honoring a real wall-clock backoff would require one. This is an honest
simplification, not a hidden shortcut: `next_retry_at` is real data, the
Engine just doesn't wait for it to arrive. Retry can repeat a node's side
effect (DESIGN.md §7: retry is the Engine's job, not the provider's, but
neither layer promises exactly-once) — `retry.derive_idempotency_key` gives a
stable key per logical attempt so a future real Tool/Provider integration
*could* deduplicate, but V1's Mock provider/tools have no external state to
dedupe against, so no dedup actually happens today.

Fase 6 adds `resume()`: reaching a HumanApprovalNode still suspends the run
(`run()` returns with `execution.state == WAITING` and an `ApprovalRequest`
attached to the result), but an external approve/reject decision can now be
fed back in via `resume()`, which validates it through the exact same
`domain.approve()`/`domain.reject()` functions Fase 1 already defined (no
second state machine, per the Fase 6 brief) and continues the graph traversal
from the node after the approval node.

Fase 9 makes the Engine emit the full DESIGN.md §11.1 event catalog (previously
only APPROVAL_REQUESTED and NODE_RETRYING existed): EXECUTION_STARTED,
EXECUTION_RESUMED, NODE_STARTED, NODE_COMPLETED, NODE_FAILED,
CONDITION_EVALUATED, APPROVAL_APPROVED/REJECTED, EXECUTION_COMPLETED,
EXECUTION_FAILED. `EXECUTION_STARTED`'s payload carries `trigger_input` and
`NODE_COMPLETED`'s carries `context_patch` specifically so that
`engine.replay` can reconstruct an execution's final context from nothing but
its event log (DESIGN.md §13). One known gap: `cancel()` does not emit
EXECUTION_CANCELLED — it only ever returns a new `Execution`, and wiring an
event through that call would change its signature; documented as a Fase 9
limitation rather than done partially.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from dataclasses import replace as dataclass_replace
from datetime import UTC, datetime
from typing import Literal

from workflow_engine.domain import (
    ApprovalRequest,
    ApprovalStatus,
    Event,
    EventType,
    Execution,
    ExecutionState,
    NodeExecution,
    WorkflowDefinition,
)
from workflow_engine.domain import approve as domain_approve
from workflow_engine.domain import reject as domain_reject
from workflow_engine.domain.enums import NodeExecutionStatus, NodeType
from workflow_engine.domain.errors import InvalidApprovalError
from workflow_engine.engine.errors import (
    DefensiveLoopLimitExceededError,
    InvalidExecutionStateError,
)
from workflow_engine.engine.next_node import build_edges_by_source, resolve_next_node_id
from workflow_engine.engine.node_executor import NodeExecutorRegistry
from workflow_engine.engine.observability import MetricsSink, NullMetricsSink, log_event
from workflow_engine.engine.retry import compute_backoff_delay, derive_idempotency_key

Clock = Callable[[], datetime]
IdFactory = Callable[[], str]
Decision = Literal["approved", "rejected"]


def _default_clock() -> datetime:
    return datetime.now(UTC)


def _default_id_factory() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class EngineRunResult:
    """Everything produced by one `ExecutionEngine.run()`/`resume()` call.
    Purely in-memory — persisting any of this is left to a later phase.
    `approval_request` is set only when this call ended in WAITING.
    """

    execution: Execution
    node_executions: list[NodeExecution] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    approval_request: ApprovalRequest | None = None
    resolved_approval: ApprovalRequest | None = None
    """Set only by `resume()`: the ApprovalRequest as it looked immediately
    after being approved/rejected (status flipped, resolved_at/by set) — the
    persistence layer needs this to persist the resolution. Distinct from
    `approval_request`, which is always about a *new* PENDING approval
    encountered by *this* call (e.g. if the resumed path reaches another
    HumanApprovalNode)."""


class ExecutionEngine:
    def __init__(
        self,
        workflow: WorkflowDefinition,
        registry: NodeExecutorRegistry,
        *,
        clock: Clock = _default_clock,
        id_factory: IdFactory = _default_id_factory,
        logger: logging.Logger | None = None,
        metrics: MetricsSink | None = None,
    ) -> None:
        self._workflow = workflow
        self._registry = registry
        self._clock = clock
        self._id_factory = id_factory
        self._logger = logger
        self._metrics: MetricsSink = metrics if metrics is not None else NullMetricsSink()
        self._nodes_by_id = {n.id: n for n in workflow.nodes}
        self._edges_by_source = build_edges_by_source(workflow.edges)
        # Process-local "has this approval already been resolved?" ledger.
        # This is what makes the concurrent-approval scenario in Fase 6 §6.6
        # (two resume() calls racing on the same originally-PENDING snapshot)
        # actually fail deterministically instead of both silently succeeding
        # against their own in-memory copies — see resume() and DESIGN.md
        # limitations. It is intentionally scoped to one ExecutionEngine
        # instance (not a module-level global): a real cross-process
        # equivalent is the persisted CAS in Fase 7, not this dict.
        self._resolved_approval_ids: dict[str, ApprovalStatus] = {}

    def _emit(
        self,
        events: list[Event],
        sequence: int,
        execution_id: str,
        event_type: EventType,
        *,
        node_id: str | None = None,
        payload: dict[str, object] | None = None,
        now: datetime,
    ) -> int:
        """Append one Event and return the incremented sequence — the single
        place `_loop`/`run`/`resume` assign event ids and sequence numbers,
        so every emission site stays a one-liner instead of repeating
        `Event(event_id=self._id_factory(), ...)` everywhere. Also the single
        place structured logging (§9.8) and metrics (§9.9) hook in, so every
        event that exists is automatically observable without every call
        site needing to remember to log/count it.
        """
        sequence += 1
        event = Event(
            event_id=self._id_factory(),
            execution_id=execution_id,
            sequence=sequence,
            event_type=event_type,
            node_id=node_id,
            payload=payload or {},
            created_at=now,
        )
        events.append(event)
        if self._logger is not None:
            log_event(self._logger, event)
        self._metrics.increment(f"workflow_engine.{event_type.value}")
        return sequence

    def run(self, execution: Execution) -> EngineRunResult:
        """Advance `execution` until it is COMPLETED, FAILED, or WAITING.

        Raises InvalidExecutionStateError if `execution` is not PENDING or
        RUNNING (e.g. WAITING or already terminal — resuming a WAITING
        execution is `resume()`'s job, not `run()`'s). Raises
        UnknownNodeExecutorError / NoMatchingBranchError /
        DefensiveLoopLimitExceededError as configuration or graph-integrity
        problems distinct from a node's own runtime failure — these are never
        swallowed into a FAILED execution.
        """
        if execution.workflow_definition_id != self._workflow.id:
            raise InvalidExecutionStateError(
                f"execution {execution.id!r} targets workflow "
                f"{execution.workflow_definition_id!r}, not {self._workflow.id!r}"
            )

        events: list[Event] = []
        sequence = 0

        if execution.state == ExecutionState.PENDING:
            now = self._clock()
            execution = execution.transition_to(ExecutionState.RUNNING, now=now)
            execution = execution.with_frontier([self._workflow.start_node_id], now=now)
            sequence = self._emit(
                events,
                sequence,
                execution.id,
                EventType.EXECUTION_STARTED,
                payload={"trigger_input": execution.context.trigger_input},
                now=now,
            )
        elif execution.state != ExecutionState.RUNNING:
            raise InvalidExecutionStateError(
                f"cannot run execution {execution.id!r}: state is "
                f"{execution.state.value}, expected PENDING or RUNNING"
            )

        return self._loop(execution, execution.current_node_ids[0], [], events, sequence)

    def resume(
        self,
        execution: Execution,
        approval: ApprovalRequest,
        *,
        decision: Decision,
        resolved_by: str,
        decision_payload: dict[str, object] | None = None,
    ) -> EngineRunResult:
        """Apply an external approve/reject decision to a WAITING execution
        and continue running from the node after the HumanApprovalNode.

        Validation is delegated entirely to `domain.approve()`/`domain.reject()`
        (DESIGN.md §9, §3.3) — same preconditions as calling them directly:
        `execution.state == WAITING`, `approval.status == PENDING`, and
        `approval.execution_id == execution.id`. On top of that, Fase 6 §6.7
        adds one more check `domain.approve()` cannot make on its own because
        it doesn't know about the graph: `approval.node_id` must be the node
        the execution's frontier is actually waiting on — a caller passing an
        `ApprovalRequest` with the right `execution_id` but a fabricated
        `node_id` is rejected rather than trusted.
        """
        if not execution.current_node_ids or approval.node_id != execution.current_node_ids[0]:
            raise InvalidApprovalError(
                f"approval {approval.approval_id!r} targets node {approval.node_id!r}, "
                f"but execution {execution.id!r} is waiting on "
                f"{execution.current_node_ids!r}"
            )

        already_resolved = self._resolved_approval_ids.get(approval.approval_id)
        if already_resolved is not None:
            raise InvalidApprovalError(
                f"approval {approval.approval_id!r} was already resolved as "
                f"{already_resolved.value} by a previous resume() call"
            )

        events: list[Event] = []
        sequence = 0
        now = self._clock()
        sequence = self._emit(
            events,
            sequence,
            execution.id,
            EventType.EXECUTION_RESUMED,
            node_id=approval.node_id,
            now=now,
        )

        resolver = domain_approve if decision == "approved" else domain_reject
        new_approval, execution = resolver(
            approval, execution, resolved_by=resolved_by, decision_payload=decision_payload, now=now
        )
        self._resolved_approval_ids[approval.approval_id] = new_approval.status
        sequence = self._emit(
            events,
            sequence,
            execution.id,
            EventType.APPROVAL_APPROVED if decision == "approved" else EventType.APPROVAL_REJECTED,
            node_id=approval.node_id,
            payload={"resolved_by": resolved_by, "approval_id": approval.approval_id},
            now=now,
        )

        node = self._nodes_by_id[approval.node_id]
        decision_output = {"decision": decision, "resolved_by": resolved_by}
        new_context = execution.context.with_patch({node.id: decision_output})
        execution = execution.with_context(new_context, now=now)

        resolved_node_execution = (
            NodeExecution(
                id=self._id_factory(),
                execution_id=execution.id,
                node_id=node.id,
                idempotency_key=f"{execution.id}:{node.id}:resume:{approval.approval_id}",
                input=dict(execution.context.variables),
            )
            .start(now=now)
            .complete(decision_output, now=now)
        )
        sequence = self._emit(
            events,
            sequence,
            execution.id,
            EventType.NODE_COMPLETED,
            node_id=node.id,
            payload={
                "attempt": 1,
                "output": decision_output,
                "context_patch": {node.id: decision_output},
            },
            now=now,
        )

        next_node_id = resolve_next_node_id(node, self._edges_by_source, execution.context)
        if next_node_id is None:
            # Defensive only: graph validation rule 6 (DESIGN.md §4.1) gives
            # every non-END node exactly one outgoing edge, so a
            # HUMAN_APPROVAL node's `next_node_id` is never None in practice —
            # the same invariant that makes workflow.py's END-unreachable
            # fallback unreachable. Kept for symmetry with `_loop`'s handling
            # of the same case, not because it's expected to execute.
            execution = execution.with_frontier([], now=now)
            execution = execution.transition_to(ExecutionState.COMPLETED, now=now)
            sequence = self._emit(
                events, sequence, execution.id, EventType.EXECUTION_COMPLETED, now=now
            )
            return EngineRunResult(
                execution=execution,
                node_executions=[resolved_node_execution],
                events=events,
                resolved_approval=new_approval,
            )

        execution = execution.with_frontier([next_node_id], now=now)
        result = self._loop(execution, next_node_id, [resolved_node_execution], events, sequence)
        return dataclass_replace(result, resolved_approval=new_approval)

    def _loop(
        self,
        execution: Execution,
        current_node_id: str,
        node_executions: list[NodeExecution],
        events: list[Event],
        sequence: int,
    ) -> EngineRunResult:
        max_steps = len(self._workflow.nodes) + 1
        steps = 0

        while True:
            steps += 1
            if steps > max_steps:
                raise DefensiveLoopLimitExceededError(
                    f"execution {execution.id!r} exceeded {max_steps} steps "
                    f"(workflow has {len(self._workflow.nodes)} nodes) — "
                    "graph validation should make this unreachable; likely an "
                    "unvalidated (model_construct'd) cyclic graph"
                )

            node = self._nodes_by_id[current_node_id]
            executor = self._registry.resolve(node.type)
            idempotency_key = derive_idempotency_key(
                execution.id, node.id, execution.context.variables
            )

            now = self._clock()
            node_execution = NodeExecution(
                id=self._id_factory(),
                execution_id=execution.id,
                node_id=node.id,
                idempotency_key=idempotency_key,
                input=dict(execution.context.variables),
            ).start(now=now)
            sequence = self._emit(
                events,
                sequence,
                execution.id,
                EventType.NODE_STARTED,
                node_id=node.id,
                payload={"attempt": node_execution.attempt},
                now=now,
            )

            # Retry sub-loop: a node with a matching RetryPolicy is retried
            # synchronously here (see module docstring — no real scheduler,
            # no real sleep). `node_execution` is reassigned to each new
            # attempt's record; every attempt is still appended to
            # `node_executions` individually (Fase 1's write-once-per-attempt
            # contract — see NodeExecution.retry()).
            while True:
                result = executor.execute(node, execution.context)

                for draft in result.events:
                    sequence = self._emit(
                        events,
                        sequence,
                        execution.id,
                        draft.event_type,
                        node_id=draft.node_id,
                        payload=draft.payload,
                        now=now,
                    )

                if result.status != "failed":
                    break  # "completed" or "waiting" — handled below

                assert result.error is not None  # NodeResult invariant (Fase 1)
                policy = node.retry_policy
                retryable = (
                    policy is not None
                    and result.error.category in policy.retry_on
                    and node_execution.attempt < policy.max_attempts
                )
                failed_attempt = node_execution.fail(result.error, now=now)
                node_executions.append(failed_attempt)
                sequence = self._emit(
                    events,
                    sequence,
                    execution.id,
                    EventType.NODE_FAILED,
                    node_id=node.id,
                    payload={
                        "attempt": failed_attempt.attempt,
                        "error_category": result.error.category.value,
                        "error_message": result.error.message,
                    },
                    now=now,
                )

                if not retryable:
                    execution = execution.transition_to(ExecutionState.FAILED, now=now)
                    sequence = self._emit(
                        events, sequence, execution.id, EventType.EXECUTION_FAILED, now=now
                    )
                    return EngineRunResult(
                        execution=execution, node_executions=node_executions, events=events
                    )

                assert policy is not None
                delay = compute_backoff_delay(policy, node_execution.attempt)
                failed_attempt = failed_attempt.model_copy(update={"next_retry_at": now + delay})
                node_executions[-1] = failed_attempt
                sequence = self._emit(
                    events,
                    sequence,
                    execution.id,
                    EventType.NODE_RETRYING,
                    node_id=node.id,
                    payload={
                        "attempt": node_execution.attempt,
                        "next_attempt": node_execution.attempt + 1,
                        "error_category": result.error.category.value,
                    },
                    now=now,
                )

                now = self._clock()
                node_execution = failed_attempt.retry(
                    new_id=self._id_factory(), idempotency_key=idempotency_key
                ).start(now=now)
                sequence = self._emit(
                    events,
                    sequence,
                    execution.id,
                    EventType.NODE_STARTED,
                    node_id=node.id,
                    payload={"attempt": node_execution.attempt},
                    now=now,
                )
                # loop back and invoke the executor again for the new attempt

            if result.status == "completed":
                node_execution = node_execution.complete(result.output or {}, now=now)
                node_executions.append(node_execution)
                sequence = self._emit(
                    events,
                    sequence,
                    execution.id,
                    EventType.NODE_COMPLETED,
                    node_id=node.id,
                    payload={
                        "attempt": node_execution.attempt,
                        "output": result.output,
                        "context_patch": result.context_patch,
                    },
                    now=now,
                )

                new_context = execution.context.with_patch(result.context_patch)
                execution = execution.with_context(new_context, now=now)

                next_node_id = resolve_next_node_id(node, self._edges_by_source, execution.context)
                if node.type == NodeType.CONDITION:
                    sequence = self._emit(
                        events,
                        sequence,
                        execution.id,
                        EventType.CONDITION_EVALUATED,
                        node_id=node.id,
                        payload={"result_node_id": next_node_id},
                        now=now,
                    )
                if next_node_id is None:
                    execution = execution.with_frontier([], now=now)
                    execution = execution.transition_to(ExecutionState.COMPLETED, now=now)
                    sequence = self._emit(
                        events, sequence, execution.id, EventType.EXECUTION_COMPLETED, now=now
                    )
                    break

                execution = execution.with_frontier([next_node_id], now=now)
                current_node_id = next_node_id
                continue

            # result.status == "waiting" (the only case left, since "failed"
            # either returned above or looped back to retry)
            assert result.error is None  # NodeResult invariant (Fase 1)
            node_execution = node_execution.transition_to(NodeExecutionStatus.WAITING)
            node_executions.append(node_execution)
            execution = execution.with_frontier([node.id], now=now)
            execution = execution.transition_to(ExecutionState.WAITING, now=now)

            approval_request = ApprovalRequest(
                approval_id=self._id_factory(),
                execution_id=execution.id,
                node_id=node.id,
                node_execution_id=node_execution.id,
                requested_at=now,
            )
            return EngineRunResult(
                execution=execution,
                node_executions=node_executions,
                events=events,
                approval_request=approval_request,
            )

        return EngineRunResult(execution=execution, node_executions=node_executions, events=events)

    def cancel(self, execution: Execution) -> Execution:
        """Cancel `execution`. A thin wrapper over the domain state machine —
        PENDING/RUNNING/WAITING -> CANCELLED are the only valid sources
        (DESIGN.md §3.1); cancelling a terminal execution raises
        InvalidStateTransitionError from the domain layer, unchanged.
        """
        return execution.transition_to(ExecutionState.CANCELLED, now=self._clock())
