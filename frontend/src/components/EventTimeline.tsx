import type { EventResponse } from "../types";

export function EventTimeline({ events }: { events: EventResponse[] }) {
  if (events.length === 0) {
    return (
      <div className="panel">
        <p className="section-title">Event timeline</p>
        <p className="empty-state">No events yet.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <p className="section-title">Event timeline</p>
      <ul className="event-list">
        {events.map((event) => (
          <li key={event.event_id}>
            <span className="event-seq">{event.sequence}</span>
            <span className="event-type">{event.event_type}</span>
            {event.node_id && <span className="event-node">{event.node_id}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
