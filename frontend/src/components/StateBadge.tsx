import type { ExecutionState } from "../types";

export function StateBadge({ state }: { state: ExecutionState }) {
  return <span className={`state-badge ${state}`}>{state}</span>;
}
