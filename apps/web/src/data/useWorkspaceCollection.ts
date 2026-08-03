import { useCallback, useEffect, useRef, useState } from "react";
import { repositoryWorkspaceGateway, workspaceEventsUrl } from "./workspaceApi";
import { messageFrom } from "../lib/apiError";
import type { WorkspaceSummary } from "../lib/types";

type WorkspaceCollectionState = {
  status: "loading" | "ready" | "error";
  workspaces: WorkspaceSummary[];
  error: string | null;
  live: boolean;
};

export function useWorkspaceCollection(): WorkspaceCollectionState & {
  refresh: () => Promise<void>;
} {
  const [state, setState] = useState<WorkspaceCollectionState>({
    status: "loading",
    workspaces: [],
    error: null,
    live: true,
  });
  // Three callers can refresh at once (mount, SSE, post-mutation). Without a
  // sequence number a slow early response can land last and overwrite newer
  // summaries, which then drives a refetch of a stale version hash.
  const latestRequestRef = useRef(0);

  const loadWorkspaces = useCallback(async () => {
    const requestId = ++latestRequestRef.current;
    try {
      const workspaces = (await repositoryWorkspaceGateway.listWorkspaceSummaries()).sort(
        (left, right) => timestamp(right.updated_at) - timestamp(left.updated_at),
      );
      if (requestId !== latestRequestRef.current) return;
      setState((current) => ({ ...current, status: "ready", workspaces, error: null }));
    } catch (error) {
      if (requestId !== latestRequestRef.current) return;
      // A failed refresh must not discard workspaces already on screen; the
      // canvas stays usable and the error is reported alongside it.
      setState((current) => ({
        ...current,
        status: current.workspaces.length ? "ready" : "error",
        error: messageFrom(error),
      }));
    }
  }, []);

  useEffect(() => {
    let active = true;

    void loadWorkspaces();
    const events = new EventSource(workspaceEventsUrl);
    events.addEventListener("workspaces_updated", () => {
      if (active) {
        void loadWorkspaces();
      }
    });
    events.onopen = () => {
      if (active) setState((current) => ({ ...current, live: true }));
    };
    events.onerror = () => {
      // EventSource reconnects on its own; surface the gap rather than fail.
      if (active && events.readyState !== EventSource.OPEN) {
        setState((current) => ({ ...current, live: false }));
      }
    };

    return () => {
      active = false;
      events.close();
    };
  }, [loadWorkspaces]);

  return { ...state, refresh: loadWorkspaces };
}

function timestamp(value: string | null): number {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
