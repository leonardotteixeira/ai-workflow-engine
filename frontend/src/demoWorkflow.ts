import type { CreateWorkflowRequest } from "./api/client";

// Self-contained demo: works with zero backend configuration (the default
// Mock LLM provider has no canned responses, so this uses a deterministic
// TRANSFORM node where a real deployment would use an LLM node — the
// Python demo script in the repository root shows the full LLM+Tool+HITL
// combination with a properly configured provider; this one only needs to
// prove the UI works end to end against a running API).
export const DEMO_WORKFLOW: CreateWorkflowRequest = {
  id: "demo-risk-review",
  version: 1,
  name: "Risk Review Demo",
  start_node_id: "start",
  nodes: [
    { id: "start", type: "START", config: {} },
    { id: "analyze", type: "TRANSFORM", config: { set: { risk_score: 87 } } },
    { id: "route", type: "CONDITION", config: {} },
    { id: "approval", type: "HUMAN_APPROVAL", config: {} },
    { id: "route_decision", type: "CONDITION", config: {} },
    { id: "report", type: "TRANSFORM", config: { set: { reported: true } } },
    { id: "end_approved", type: "END", config: {} },
    { id: "end_rejected", type: "END", config: {} },
    { id: "end_low", type: "END", config: {} },
  ],
  edges: [
    { id: "e1", source_node_id: "start", target_node_id: "analyze" },
    { id: "e2", source_node_id: "analyze", target_node_id: "route" },
    {
      id: "e3",
      source_node_id: "route",
      target_node_id: "approval",
      condition: { field: "risk_score", operator: "gte", value: 80 },
    },
    { id: "e4", source_node_id: "route", target_node_id: "end_low" },
    { id: "e5", source_node_id: "approval", target_node_id: "route_decision" },
    {
      id: "e6",
      source_node_id: "route_decision",
      target_node_id: "report",
      condition: { field: "approval.decision", operator: "eq", value: "approved" },
    },
    { id: "e7", source_node_id: "route_decision", target_node_id: "end_rejected" },
    { id: "e8", source_node_id: "report", target_node_id: "end_approved" },
  ],
};
