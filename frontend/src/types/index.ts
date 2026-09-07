// Mirrors src/workflow_engine/api/schemas.py — kept in sync by hand since
// this project has no shared codegen step. If these drift from the backend,
// the API client's runtime behavior (not just types) will surface it via a
// failed response shape, not silently.

export type NodeType =
  | "START"
  | "LLM"
  | "TOOL"
  | "CONDITION"
  | "TRANSFORM"
  | "HUMAN_APPROVAL"
  | "END";

export type ExecutionState =
  | "PENDING"
  | "RUNNING"
  | "WAITING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type EventType =
  | "execution_started"
  | "execution_completed"
  | "execution_failed"
  | "execution_cancelled"
  | "execution_resumed"
  | "node_started"
  | "node_completed"
  | "node_failed"
  | "node_retrying"
  | "node_skipped"
  | "condition_evaluated"
  | "approval_requested"
  | "approval_approved"
  | "approval_rejected"
  | "approval_expired";

export interface WorkflowNode {
  id: string;
  type: NodeType;
  config: Record<string, unknown>;
}

export interface WorkflowEdge {
  id: string;
  source_node_id: string;
  target_node_id: string;
  condition?: unknown;
}

export interface WorkflowResponse {
  id: string;
  version: number;
  name: string;
  checksum: string;
  node_count: number;
  edge_count: number;
  start_node_id: string;
  created_at: string;
}

export interface WaitingApproval {
  approval_id: string;
  node_id: string;
}

export interface ExecutionResponse {
  id: string;
  workflow_definition_id: string;
  workflow_version: number;
  state: ExecutionState;
  current_node_ids: string[];
  context_variables: Record<string, unknown>;
  version: number;
  created_at: string;
  updated_at: string;
  waiting_approval: WaitingApproval | null;
}

export interface EventResponse {
  event_id: string;
  execution_id: string;
  sequence: number;
  event_type: EventType;
  node_id: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface NodeHistoryEntry {
  node_id: string;
  event_type: EventType;
  sequence: number;
  attempt: number | null;
}

export interface ReplayResponse {
  execution_id: string;
  trigger_input: Record<string, unknown>;
  variables: Record<string, unknown>;
  node_history: NodeHistoryEntry[];
  state: ExecutionState | null;
}

export interface ApiErrorBody {
  detail: string;
}
