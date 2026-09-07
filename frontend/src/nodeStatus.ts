import type { EventResponse, ExecutionResponse } from "./types";

/** Derives a per-node visual status purely from the events already fetched
 * — no separate "node status" endpoint exists, and none is needed since the
 * event log already carries this information (Fase 11 §11.5: "usar os
 * eventos realmente existentes, não inventar dados"). */
export function deriveNodeStatuses(
  events: EventResponse[],
  execution: ExecutionResponse | null,
): Record<string, string> {
  const statuses: Record<string, string> = {};
  for (const event of events) {
    if (!event.node_id) continue;
    if (event.event_type === "node_started") statuses[event.node_id] = "RUNNING";
    else if (event.event_type === "node_completed") statuses[event.node_id] = "COMPLETED";
    else if (event.event_type === "node_failed") statuses[event.node_id] = "FAILED";
    else if (event.event_type === "approval_requested") statuses[event.node_id] = "WAITING";
  }
  if (execution) {
    for (const nodeId of execution.current_node_ids) {
      if (execution.state === "WAITING") statuses[nodeId] = "WAITING";
    }
  }
  return statuses;
}
