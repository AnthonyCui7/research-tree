import { useCallback, useEffect, useRef, useState } from "react";
import { repositoryWorkspaceGateway, workspaceEventsUrl } from "./workspaceApi";
import { messageFrom } from "../lib/apiError";
import { loadSession, sessionState } from "./session";
import type { WorkspaceSummary } from "../lib/types";

// EventSource reconnects on its own after a dropped connection but gives up
// for good on a response it cannot use (a 5xx from the ingress during a
// rollout, a 401 once the session ends). The session is rechecked first; if
// it still stands, the stream is opened again, waiting a little longer each
// time so a deploy in progress is not hammered.
const RECONNECT_DELAYS_MS = [2_000, 5_000, 10_000, 30_000];

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
      // canvas stays usable and AppShell reports the error in a dismissible
      // strip over it.
      setState((current) => ({
        ...current,
        status: current.workspaces.length ? "ready" : "error",
        error: messageFrom(error),
      }));
    }
  }, []);

  useEffect(() => {
    let active = true;
    let events: EventSource | null = null;
    let retry: number | null = null;
    let attempts = 0;

    function connect() {
      events = new EventSource(workspaceEventsUrl);
      events.addEventListener("workspaces_updated", () => {
        if (active) void loadWorkspaces();
      });
      events.onopen = () => {
        if (!active) return;
        attempts = 0;
        setState((current) => ({ ...current, live: true }));
        // Anything that changed while the stream was down is fetched now.
        void loadWorkspaces();
      };
      events.onerror = () => {
        if (!active || !events) return;
        if (events.readyState !== EventSource.OPEN) {
          setState((current) => ({ ...current, live: false }));
        }
        if (events.readyState !== EventSource.CLOSED) return;
        events.close();
        events = null;
        void loadSession({ recheck: true }).then(() => {
          // An ended session unmounts the app, and this effect with it.
          if (!active || sessionState().status !== "ready") return;
          const delay = RECONNECT_DELAYS_MS[Math.min(attempts, RECONNECT_DELAYS_MS.length - 1)];
          attempts += 1;
          retry = window.setTimeout(connect, delay);
        });
      };
    }

    void loadWorkspaces();
    connect();

    return () => {
      active = false;
      if (retry !== null) window.clearTimeout(retry);
      events?.close();
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
