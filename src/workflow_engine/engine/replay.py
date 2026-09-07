"""Replay — DESIGN.md §13, Fase 9 §9.5/§9.6.

Replay ≠ rerun. Replay takes an execution's `Event` log and reconstructs a
*view* of its final state — the accumulated context variables, the node
history, the terminal outcome — by folding over events already recorded. It
NEVER calls an `LLMProvider`, a `Tool`, or the `ExecutionEngine` itself: no
network, no database write, no side effect of any kind. If replay needed a
live LLM/tool to reproduce a run, it wouldn't be reconstruction, it would be
a second, possibly-different execution (DESIGN.md §13, restated in Fase 9's
own brief: "Replay NÃO DEVE chamar OpenAI/ferramentas externas/fazer
HTTP/alterar banco/produzir side effects").

Because event payloads carry the same `context_patch` the Engine already
merged into the live `Execution.context` when it happened (Fase 9's
instrumentation in engine.py), replaying every `NODE_COMPLETED` and
`APPROVAL_APPROVED`/`APPROVAL_REJECTED`-adjacent `NODE_COMPLETED` event in
order reconstructs the same `variables` dict the real execution had —
without needing the original `Execution` row at all. This is what makes
replay useful for audit even in a scenario where you only have the event log
(e.g. it's the thing being audited).

What replay does NOT reconstruct: `Execution.version`, `created_at`,
`updated_at`, or the exact `NodeExecution`/`ApprovalRequest` rows — those live
in their own tables, not the event log, by DESIGN.md's own model separation
(events are a record of *facts*, not the primary store of *current state*).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from workflow_engine.domain import Event, EventType
from workflow_engine.domain.enums import ExecutionState

_TERMINAL_EVENT_STATES: dict[EventType, ExecutionState] = {
    EventType.EXECUTION_COMPLETED: ExecutionState.COMPLETED,
    EventType.EXECUTION_FAILED: ExecutionState.FAILED,
    EventType.EXECUTION_CANCELLED: ExecutionState.CANCELLED,
}

_WAITING_EVENT = EventType.APPROVAL_REQUESTED


class ReplayError(Exception):
    """Raised when an event log is structurally invalid and cannot be
    replayed safely. Fase 9 §9.12: replay must fail loudly on corruption,
    never produce a state that merely *looks* valid.
    """


@dataclass(frozen=True)
class NodeHistoryEntry:
    node_id: str
    event_type: EventType
    sequence: int
    attempt: int | None = None


@dataclass(frozen=True)
class ReplayedExecution:
    execution_id: str
    trigger_input: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)
    node_history: list[NodeHistoryEntry] = field(default_factory=list)
    state: ExecutionState | None = None
    """None if the log doesn't (yet) contain a terminal or waiting event —
    e.g. a truncated/mid-flight log. Not an error by itself. Otherwise the
    *last* lifecycle event's implied state — WAITING after an
    APPROVAL_REQUESTED, RUNNING after an EXECUTION_RESUMED, or a terminal
    state after EXECUTION_COMPLETED/FAILED/CANCELLED, whichever came last."""


def validate_event_sequence(events: list[Event]) -> None:
    """Fase 9 §9.12: sequence must start at 1, be strictly increasing by
    exactly 1 each step, with no gaps and no duplicates, and every event must
    belong to the same execution_id. Raises ReplayError otherwise.
    """
    if not events:
        return
    execution_ids = {e.execution_id for e in events}
    if len(execution_ids) > 1:
        raise ReplayError(
            f"event log mixes multiple execution_ids: {sorted(execution_ids)} — "
            "replay operates on a single execution's history"
        )

    ordered = sorted(events, key=lambda e: e.sequence)
    expected = 1
    for event in ordered:
        if event.sequence != expected:
            if event.sequence < expected:
                raise ReplayError(
                    f"duplicated or out-of-order sequence: expected {expected}, "
                    f"found {event.sequence} (event_id={event.event_id!r})"
                )
            raise ReplayError(
                f"gap in event sequence: expected {expected}, found {event.sequence} "
                f"(event_id={event.event_id!r})"
            )
        expected += 1


def replay(events: list[Event]) -> ReplayedExecution:
    """Reconstruct a `ReplayedExecution` from a complete, ordered event log.

    Raises ReplayError if the sequence is corrupted (see
    `validate_event_sequence`). An empty list is valid and reconstructs an
    "unstarted" view (`state=None`, empty variables) — that's a legitimate
    state (an Execution row inserted but never run), not corruption.
    """
    validate_event_sequence(events)
    if not events:
        return ReplayedExecution(execution_id="")

    ordered = sorted(events, key=lambda e: e.sequence)
    execution_id = ordered[0].execution_id

    trigger_input: dict[str, Any] = {}
    variables: dict[str, Any] = {}
    history: list[NodeHistoryEntry] = []
    state: ExecutionState | None = None

    for event in ordered:
        if event.event_type == EventType.EXECUTION_STARTED:
            payload_trigger = event.payload.get("trigger_input")
            if not isinstance(payload_trigger, dict):
                raise ReplayError(
                    f"malformed EXECUTION_STARTED payload (event_id={event.event_id!r}): "
                    "missing/invalid 'trigger_input'"
                )
            trigger_input = payload_trigger
        elif event.event_type == EventType.NODE_COMPLETED:
            patch = event.payload.get("context_patch")
            if not isinstance(patch, dict):
                raise ReplayError(
                    f"malformed NODE_COMPLETED payload (event_id={event.event_id!r}): "
                    "missing/invalid 'context_patch'"
                )
            variables = {**variables, **patch}
            if event.node_id is None:
                raise ReplayError(
                    f"NODE_COMPLETED event {event.event_id!r} is missing node_id"
                )
            history.append(
                NodeHistoryEntry(
                    node_id=event.node_id,
                    event_type=event.event_type,
                    sequence=event.sequence,
                    attempt=event.payload.get("attempt"),
                )
            )
        elif event.event_type in (EventType.NODE_FAILED, EventType.NODE_STARTED):
            if event.node_id is None:
                raise ReplayError(
                    f"{event.event_type.value} event {event.event_id!r} missing node_id"
                )
            history.append(
                NodeHistoryEntry(
                    node_id=event.node_id,
                    event_type=event.event_type,
                    sequence=event.sequence,
                    attempt=event.payload.get("attempt"),
                )
            )
        elif event.event_type == _WAITING_EVENT:
            state = ExecutionState.WAITING
        elif event.event_type in _TERMINAL_EVENT_STATES:
            state = _TERMINAL_EVENT_STATES[event.event_type]
        elif event.event_type == EventType.EXECUTION_RESUMED:
            state = ExecutionState.RUNNING

    return ReplayedExecution(
        execution_id=execution_id,
        trigger_input=trigger_input,
        variables=variables,
        node_history=history,
        state=state,
    )
