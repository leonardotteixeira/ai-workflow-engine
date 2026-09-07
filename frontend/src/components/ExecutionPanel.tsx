import type { ExecutionResponse } from "../types";
import { StateBadge } from "./StateBadge";

export function ExecutionPanel({ execution }: { execution: ExecutionResponse | null }) {
  if (!execution) {
    return (
      <div className="panel">
        <p className="section-title">Execution</p>
        <p className="empty-state">Start an execution to see its state here.</p>
      </div>
    );
  }

  const elapsedMs = new Date(execution.updated_at).getTime() - new Date(execution.created_at).getTime();

  return (
    <div className="panel">
      <p className="section-title">Execution</p>
      <div style={{ marginBottom: 10 }}>
        <StateBadge state={execution.state} />
      </div>
      <dl className="kv-list">
        <dt>ID</dt>
        <dd>{execution.id}</dd>
        <dt>Workflow</dt>
        <dd>
          {execution.workflow_definition_id} v{execution.workflow_version}
        </dd>
        <dt>Current node(s)</dt>
        <dd>{execution.current_node_ids.join(", ") || "—"}</dd>
        <dt>Elapsed</dt>
        <dd>{Math.max(0, elapsedMs)} ms</dd>
      </dl>
      <p className="section-title" style={{ marginTop: 14 }}>
        Context
      </p>
      <pre className="context-json">{JSON.stringify(execution.context_variables, null, 2)}</pre>
    </div>
  );
}
