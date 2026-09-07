"""Event / EventDraft — DESIGN.md §2.3, §11.

`Event` is the fully-formed, persisted record (has an id and a sequence
number). `EventDraft` is what a Node hands back inside a `NodeResult`
(DESIGN.md §6.1) — it deliberately omits `event_id`, `execution_id` and
`sequence`, because a Node does not know its own position in the execution's
event log; only the Engine (writing through the event store) assigns those.

No event store, no persistence, no sequence-allocation mechanism here — this
module is only the shape of an event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from workflow_engine.domain.enums import EventType


class EventDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: EventType
    node_id: str | None = None
    payload: dict[str, Any] = {}


class Event(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_id: str
    execution_id: str
    sequence: int
    event_type: EventType
    node_id: str | None = None
    payload: dict[str, Any] = {}
    created_at: datetime

    @field_validator("sequence")
    @classmethod
    def _sequence_must_be_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Event.sequence must be >= 1 (monotonic per execution_id)")
        return value

    @classmethod
    def from_draft(
        cls, draft: EventDraft, *, event_id: str, execution_id: str, sequence: int, now: datetime
    ) -> Event:
        """Promote an EventDraft into a persisted Event by attaching the
        identifiers only the Engine/event store can assign.
        """
        return cls(
            event_id=event_id,
            execution_id=execution_id,
            sequence=sequence,
            event_type=draft.event_type,
            node_id=draft.node_id,
            payload=draft.payload,
            created_at=now,
        )
