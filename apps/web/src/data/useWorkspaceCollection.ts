import { useCallback, useEffect, useState } from "react";
import { repositoryWorkspaceGateway, workspaceEventsUrl } from "./workspaceApi";
import type { WorkspaceDocument } from "../lib/types";

type WorkspaceCollectionState =
  | { status: "loading"; workspaces: WorkspaceDocument[]; error: null }
  | { status: "ready"; workspaces: WorkspaceDocument[]; error: null }
  | { status: "error"; workspaces: WorkspaceDocument[]; error: string };

export function useWorkspaceCollection(): WorkspaceCollectionState & { refresh: () => Promise<void> } {
  const [state, setState] = useState<WorkspaceCollectionState>({
    status: "loading",
    workspaces: [],
    error: null,
  });

  const loadWorkspaces = useCallback(async () => {
      try {
        const workspaces = await loadRepositoryWorkspaces();
        setState({ status: "ready", workspaces, error: null });
      } catch (error) {
        setState((current) => ({
          status: "error",
          workspaces: current.workspaces,
          error: "Your workspaces could not be loaded. Refresh and try again.",
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

    return () => {
      active = false;
      events.close();
    };
  }, [loadWorkspaces]);

  return { ...state, refresh: loadWorkspaces };
}

async function loadRepositoryWorkspaces(): Promise<WorkspaceDocument[]> {
  const summaries = (await repositoryWorkspaceGateway.listWorkspaceSummaries()).sort(
    (left, right) => timestamp(right.updated_at) - timestamp(left.updated_at),
  );
  return Promise.all(
    summaries.map(async (summary) => {
      const [workspaceResponse, versions] = await Promise.all([
        repositoryWorkspaceGateway.getWorkspace(summary.workspace_id),
        repositoryWorkspaceGateway.getWorkspaceVersions(summary.workspace_id),
      ]);
      return {
        ...workspaceResponse.workspace,
        current_workspace_version_hash: workspaceResponse.workspace_version_hash,
        workspace_versions: versions,
      };
    }),
  );
}

function timestamp(value: string | null): number {
  if (!value) {
    return 0;
  }
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
}
