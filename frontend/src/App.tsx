import { useMemo, useState } from "react";
import { ApiError, api } from "./api/client";
import { ApprovalPanel } from "./components/ApprovalPanel";
import { EventTimeline } from "./components/EventTimeline";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { ReplayPanel } from "./components/ReplayPanel";
import { WorkflowCanvas } from "./components/WorkflowCanvas";
import { DEMO_WORKFLOW } from "./demoWorkflow";
import { useExecution } from "./hooks/useExecution";
import { deriveNodeStatuses } from "./nodeStatus";

export function App() {
  const [workflowRegistered, setWorkflowRegistered] = useState(false);
  const [executionId, setExecutionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const { execution, events, refresh } = useExecution(executionId);

  const nodeStatuses = useMemo(() => deriveNodeStatuses(events, execution), [events, execution]);

  const loadWorkflow = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.createWorkflow(DEMO_WORKFLOW);
      setWorkflowRegistered(true);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not register the demo workflow");
    } finally {
      setBusy(false);
    }
  };

  const startExecution = async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createExecution(DEMO_WORKFLOW.id, DEMO_WORKFLOW.version ?? 1, {});
      setExecutionId(created.id);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not start the execution");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app-shell">
      <div className="header">
        <h1>AI Workflow Engine</h1>
        <div className="header-actions">
          <button disabled={busy} onClick={loadWorkflow}>
            {workflowRegistered ? "Reload demo workflow" : "Load demo workflow"}
          </button>
          <button className="primary" disabled={busy || !workflowRegistered} onClick={startExecution}>
            Start execution
          </button>
        </div>
      </div>

      {error && <div className="error-banner" style={{ gridColumn: "1 / -1" }}>{error}</div>}

      <div className="panel canvas-panel">
        <WorkflowCanvas
          nodes={DEMO_WORKFLOW.nodes}
          edges={DEMO_WORKFLOW.edges}
          nodeStatuses={nodeStatuses}
        />
      </div>

      <div className="sidebar">
        <ExecutionPanel execution={execution} />
        {execution && <ApprovalPanel execution={execution} onResolved={refresh} />}
        <EventTimeline events={events} />
        <ReplayPanel executionId={executionId} />
      </div>
    </div>
  );
}
