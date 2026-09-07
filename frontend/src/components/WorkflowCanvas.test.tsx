import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEMO_WORKFLOW } from "../demoWorkflow";
import { WorkflowCanvas } from "./WorkflowCanvas";

describe("WorkflowCanvas", () => {
  it("shows an empty state with no nodes", () => {
    render(<WorkflowCanvas nodes={[]} edges={[]} nodeStatuses={{}} />);
    expect(screen.getByText(/no workflow loaded/i)).toBeInTheDocument();
  });

  it("renders every node's id from the workflow", () => {
    render(
      <WorkflowCanvas
        nodes={DEMO_WORKFLOW.nodes}
        edges={DEMO_WORKFLOW.edges}
        nodeStatuses={{ start: "COMPLETED" }}
      />,
    );
    for (const node of DEMO_WORKFLOW.nodes) {
      expect(screen.getAllByText(node.id).length).toBeGreaterThan(0);
    }
  });
});
