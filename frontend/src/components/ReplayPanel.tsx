import { useState } from "react";
import { api, ApiError } from "../api/client";
import type { ReplayResponse } from "../types";

export function ReplayPanel({ executionId }: { executionId: string | null }) {
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const runReplay = async () => {
    if (!executionId) return;
    setLoading(true);
    setError(null);
    try {
      setReplay(await api.replay(executionId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Replay failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="panel">
      <p className="section-title">Replay</p>
      <p style={{ fontSize: 12, color: "var(--color-text-muted)", marginTop: 0 }}>
        Reconstructs state purely from the event log — never re-invokes an LLM or tool.
      </p>
      <button disabled={!executionId || loading} onClick={runReplay}>
        {loading ? "Replaying…" : "Replay execution"}
      </button>
      {error && (
        <div className="error-banner" style={{ marginTop: 8 }}>
          {error}
        </div>
      )}
      {replay && (
        <div style={{ marginTop: 10 }}>
          <dl className="kv-list">
            <dt>Reconstructed state</dt>
            <dd>{replay.state ?? "unknown"}</dd>
          </dl>
          <pre className="context-json" style={{ marginTop: 8 }}>
            {JSON.stringify(replay.variables, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
