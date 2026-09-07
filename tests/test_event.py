from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.factories import NOW
from workflow_engine.domain import Event, EventType
from workflow_engine.domain.event import EventDraft


def test_event_from_draft_attaches_engine_assigned_identifiers() -> None:
    draft = EventDraft(event_type=EventType.NODE_STARTED, node_id="n1", payload={"attempt": 1})
    event = Event.from_draft(draft, event_id="evt-1", execution_id="exec-1", sequence=1, now=NOW)
    assert event.event_id == "evt-1"
    assert event.execution_id == "exec-1"
    assert event.sequence == 1
    assert event.event_type == EventType.NODE_STARTED
    assert event.payload == {"attempt": 1}
    assert event.created_at == NOW


@pytest.mark.parametrize("sequence", [0, -1, -100])
def test_event_sequence_must_be_positive(sequence: int) -> None:
    with pytest.raises(ValidationError):
        Event(
            event_id="e1",
            execution_id="exec-1",
            sequence=sequence,
            event_type=EventType.EXECUTION_STARTED,
            created_at=NOW,
        )


def test_events_for_same_execution_can_be_ordered_by_sequence() -> None:
    events = [
        Event(
            event_id=f"e{i}",
            execution_id="exec-1",
            sequence=i,
            event_type=EventType.NODE_STARTED,
            created_at=NOW,
        )
        for i in (3, 1, 2)
    ]
    ordered = sorted(events, key=lambda e: e.sequence)
    assert [e.event_id for e in ordered] == ["e1", "e2", "e3"]
