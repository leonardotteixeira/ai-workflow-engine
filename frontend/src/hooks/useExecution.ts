import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import type { EventResponse, ExecutionResponse } from "../types";

const ACTIVE_STATES = new Set(["PENDING", "RUNNING"]);
const POLL_INTERVAL_MS = 1200;

/** Polls GET /executions/{id} + /events while the execution is still
 * actively running, and stops automatically once it reaches WAITING or a
 * terminal state — WAITING doesn't need polling (nothing changes until a
 * human acts), and a terminal state obviously never changes again. */
export function useExecution(executionId: string | null) {
  const [execution, setExecution] = useState<ExecutionResponse | null>(null);
  const [events, setEvents] = useState<EventResponse[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refresh = async (id: string) => {
    try {
      const [exec, evts] = await Promise.all([api.getExecution(id), api.listEvents(id)]);
      setExecution(exec);
      setEvents(evts);
      setError(null);
      return exec;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Unexpected error while loading execution");
      return null;
    }
  };

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!executionId) {
      setExecution(null);
      setEvents([]);
      return;
    }

    let cancelled = false;

    const tick = async () => {
      const exec = await refresh(executionId);
      if (cancelled) return;
      if (exec && ACTIVE_STATES.has(exec.state)) {
        timerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };
    tick();

    return () => {
      cancelled = true;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [executionId]);

  return { execution, events, error, refresh: () => executionId && refresh(executionId) };
}
