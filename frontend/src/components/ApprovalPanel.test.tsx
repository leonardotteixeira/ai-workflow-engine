import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import type { ExecutionResponse } from "../types";
import { ApprovalPanel } from "./ApprovalPanel";

const waitingExecution: ExecutionResponse = {
  id: "exec-1",
  workflow_definition_id: "wf-1",
  workflow_version: 1,
  state: "WAITING",
  current_node_ids: ["approval"],
  context_variables: {},
  version: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  waiting_approval: { approval_id: "appr-1", node_id: "approval" },
};

afterEach(() => vi.restoreAllMocks());

describe("ApprovalPanel", () => {
  it("renders nothing when execution is not waiting", () => {
    const completed = { ...waitingExecution, state: "COMPLETED" as const, waiting_approval: null };
    const { container } = render(<ApprovalPanel execution={completed} onResolved={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("calls the approve endpoint and reports success", async () => {
    const approveSpy = vi.spyOn(client.api, "approve").mockResolvedValue({
      ...waitingExecution,
      state: "COMPLETED",
    });
    const onResolved = vi.fn();
    render(<ApprovalPanel execution={waitingExecution} onResolved={onResolved} />);

    fireEvent.click(screen.getByText("Approve"));

    await waitFor(() => expect(onResolved).toHaveBeenCalled());
    expect(approveSpy).toHaveBeenCalledWith("exec-1", "appr-1", "reviewer@example.com");
  });

  it("shows an error message when the API call fails", async () => {
    vi.spyOn(client.api, "reject").mockRejectedValue(new client.ApiError(409, "already resolved"));
    render(<ApprovalPanel execution={waitingExecution} onResolved={() => {}} />);

    fireEvent.click(screen.getByText("Reject"));

    await waitFor(() => expect(screen.getByText("already resolved")).toBeInTheDocument());
  });
});
