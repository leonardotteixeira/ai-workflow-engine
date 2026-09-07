"""Execution / ExecutionContext — DESIGN.md §2.2.

Both are immutable (frozen) and functional: every "change" returns a new
instance rather than mutating in place, so nothing in the domain layer ever
depends on implicit/hidden mutation. Timestamps are passed in by the caller
(never `datetime.now()` inside the domain) so transitions stay deterministic
and testable without monkeypatching a clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from workflow_engine.domain.enums import ExecutionState
from workflow_engine.domain.state_machine import EXECUTION_TRANSITIONS, assert_transition_allowed


class ExecutionContext(BaseModel):
    """Pure data. No execution logic lives here — a Node returns a `context_patch`
    (DESIGN.md §6.1) and only the Engine (a later phase) decides how/when to
    apply it via `with_patch`. `trigger_input` is set once at execution start
    and never changes.
    """

    model_config = ConfigDict(frozen=True)

    variables: dict[str, Any] = {}
    trigger_input: dict[str, Any] = {}

    def with_patch(self, patch: dict[str, Any]) -> ExecutionContext:
        """Return a new context with `patch` shallow-merged into `variables`.

        Merge only, never delete: a Node cannot remove another node's output
        by omission. Keys in `patch` override existing keys of the same name.
        """
        return self.model_copy(update={"variables": {**self.variables, **patch}})


class Execution(BaseModel):
    """DESIGN.md §2.2. `current_node_ids` is the execution frontier — a list
    because a future fan-out phase may need more than one, but V1 always keeps
    exactly one element while `state` is RUNNING/WAITING, and none once terminal.
    `version` exists purely to carry the optimistic-concurrency contract into
    the domain type; the actual compare-and-swap UPDATE is a persistence-layer
    concern (DESIGN.md §10) — the domain only guarantees `version` increments
    by exactly one per accepted transition.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    workflow_definition_id: str
    workflow_version: int
    state: ExecutionState = ExecutionState.PENDING
    context: ExecutionContext
    current_node_ids: list[str] = []
    created_at: datetime
    updated_at: datetime
    version: int = 1

    def transition_to(self, target: ExecutionState, *, now: datetime) -> Execution:
        """Validate and apply a state transition, returning a new Execution.

        Raises InvalidStateTransitionError via the shared state machine table
        (DESIGN.md §3.1) if `target` is not reachable from the current state.
        """
        assert_transition_allowed("Execution", self.state, target, EXECUTION_TRANSITIONS)
        return self.model_copy(
            update={"state": target, "updated_at": now, "version": self.version + 1}
        )

    def with_context(self, context: ExecutionContext, *, now: datetime) -> Execution:
        """Replace the context (e.g. after the Engine applies a node's context_patch)."""
        return self.model_copy(
            update={"context": context, "updated_at": now, "version": self.version + 1}
        )

    def with_frontier(self, node_ids: list[str], *, now: datetime) -> Execution:
        """Replace the execution frontier (which node(s) run next)."""
        return self.model_copy(
            update={
                "current_node_ids": list(node_ids),
                "updated_at": now,
                "version": self.version + 1,
            }
        )
