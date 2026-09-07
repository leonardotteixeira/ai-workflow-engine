import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ExecutionResponse } from "../types";
import { ExecutionPanel } from "./ExecutionPanel";

const execution: ExecutionResponse = {
  id: "exec-1",
  workflow_definition_id: "wf-1",
  workflow_version: 1,
  state: "COMPLETED",
  current_node_ids: [],
  context_variables: { risk_score: 91 },
  version: 5,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:01Z",
  waiting_approval: null,
};

describe("ExecutionPanel", () => {
  it("shows an empty state when there is no execution", () => {
    render(<ExecutionPanel execution={null} />);
    expect(screen.getByText(/start an execution/i)).toBeInTheDocument();
  });

  it("renders execution details and context", () => {
    render(<ExecutionPanel execution={execution} />);
    expect(screen.getByText("COMPLETED")).toBeInTheDocument();
    expect(screen.getByText("exec-1")).toBeInTheDocument();
    expect(screen.getByText(/risk_score/)).toBeInTheDocument();
  });
});
