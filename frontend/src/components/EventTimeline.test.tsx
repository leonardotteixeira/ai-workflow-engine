import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EventResponse } from "../types";
import { EventTimeline } from "./EventTimeline";

const events: EventResponse[] = [
  {
    event_id: "e1",
    execution_id: "exec-1",
    sequence: 1,
    event_type: "execution_started",
    node_id: null,
    payload: {},
    created_at: "2026-01-01T00:00:00Z",
  },
  {
    event_id: "e2",
    execution_id: "exec-1",
    sequence: 2,
    event_type: "node_started",
    node_id: "start",
    payload: {},
    created_at: "2026-01-01T00:00:01Z",
  },
];

describe("EventTimeline", () => {
  it("shows an empty state with no events", () => {
    render(<EventTimeline events={[]} />);
    expect(screen.getByText(/no events yet/i)).toBeInTheDocument();
  });

  it("renders events in order with type and node", () => {
    render(<EventTimeline events={events} />);
    expect(screen.getByText("execution_started")).toBeInTheDocument();
    expect(screen.getByText("node_started")).toBeInTheDocument();
    expect(screen.getByText("start")).toBeInTheDocument();
  });
});
