import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { ExecutionResponse } from "../types";

interface Props {
  execution: ExecutionResponse;
  onResolved: () => void;
}

export function ApprovalPanel({ execution, onResolved }: Props) {
  const [resolvedBy, setResolvedBy] = useState("reviewer@example.com");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (execution.state !== "WAITING" || !execution.waiting_approval) return null;
  const approval = execution.waiting_approval;

  const act = async (decision: "approve" | "reject") => {
    setPending(true);
    setError(null);
    try {
      if (decision === "approve") {
        await api.approve(execution.id, approval.approval_id, resolvedBy);
      } else {
        await api.reject(execution.id, approval.approval_id, resolvedBy);
      }
      onResolved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not resolve the approval");
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="approval-banner">
      <strong>Human approval required</strong>
      <p style={{ margin: 0, fontSize: 13 }}>
        Node <code>{approval.node_id}</code> is waiting on a decision.
      </p>
      <label style={{ fontSize: 12 }}>
        Resolved by
        <input
          type="text"
          value={resolvedBy}
          onChange={(e) => setResolvedBy(e.target.value)}
          style={{ display: "block", width: "100%", marginTop: 4, padding: 6 }}
        />
      </label>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="approve" disabled={pending} onClick={() => act("approve")}>
          Approve
        </button>
        <button className="reject" disabled={pending} onClick={() => act("reject")}>
          Reject
        </button>
      </div>
      {error && <div className="error-banner">{error}</div>}
    </div>
  );
}
