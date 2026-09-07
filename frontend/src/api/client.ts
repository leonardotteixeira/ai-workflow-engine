// Centralized API client (Fase 11 §11.10) — every HTTP call in the app goes
// through here, not scattered across components. `ApiError` carries the
// server's own `detail` message (never a raw fetch/network exception string,
// so the UI never accidentally surfaces something like a stack trace).

import type {
  ExecutionResponse,
  EventResponse,
  ReplayResponse,
  WorkflowNode,
  WorkflowEdge,
  WorkflowResponse,
} from "../types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the API running?");
  }

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      // response wasn't JSON — keep the generic message, never surface raw text
    }
    throw new ApiError(response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export interface CreateWorkflowRequest {
  id: string;
  version?: number;
  name: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  start_node_id: string;
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  createWorkflow: (body: CreateWorkflowRequest) =>
    request<WorkflowResponse>("/workflows", { method: "POST", body: JSON.stringify(body) }),

  getWorkflow: (workflowId: string, version = 1) =>
    request<WorkflowResponse>(`/workflows/${encodeURIComponent(workflowId)}?version=${version}`),

  createExecution: (workflowId: string, workflowVersion: number, triggerInput: Record<string, unknown>) =>
    request<ExecutionResponse>("/executions", {
      method: "POST",
      body: JSON.stringify({
        workflow_id: workflowId,
        workflow_version: workflowVersion,
        trigger_input: triggerInput,
      }),
    }),

  getExecution: (executionId: string) =>
    request<ExecutionResponse>(`/executions/${encodeURIComponent(executionId)}`),

  listEvents: (executionId: string) =>
    request<EventResponse[]>(`/executions/${encodeURIComponent(executionId)}/events`),

  replay: (executionId: string) =>
    request<ReplayResponse>(`/executions/${encodeURIComponent(executionId)}/replay`),

  approve: (executionId: string, approvalId: string, resolvedBy: string) =>
    request<ExecutionResponse>(`/executions/${encodeURIComponent(executionId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId, resolved_by: resolvedBy }),
    }),

  reject: (executionId: string, approvalId: string, resolvedBy: string) =>
    request<ExecutionResponse>(`/executions/${encodeURIComponent(executionId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ approval_id: approvalId, resolved_by: resolvedBy }),
    }),
};
