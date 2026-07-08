import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "./workspaceApi";
import type { WorkspaceDocument } from "../lib/types";

type WorkspaceCollectionState =
  | { status: "loading"; workspaces: WorkspaceDocument[]; error: null }
  | { status: "ready"; workspaces: WorkspaceDocument[]; error: null }
  | { status: "error"; workspaces: WorkspaceDocument[]; error: string };

export function useWorkspaceCollection(): WorkspaceCollectionState {
  const [state, setState] = useState<WorkspaceCollectionState>({
    status: "loading",
    workspaces: [],
    error: null,
  });

  useEffect(() => {
    let active = true;

    async function loadWorkspaces() {
      try {
        const workspaces = await loadRepositoryWorkspaces();
        if (active) {
          setState({ status: "ready", workspaces, error: null });
        }
      } catch (error) {
        if (active) {
          setState({
            status: "error",
            workspaces: [],
            error: error instanceof Error ? error.message : "Unknown workspace loading error.",
          });
        }
      }
    }

    void loadWorkspaces();

    return () => {
      active = false;
    };
  }, []);

  return state;
}

async function loadRepositoryWorkspaces(): Promise<WorkspaceDocument[]> {
  const summaries = await repositoryWorkspaceGateway.listWorkspaceSummaries();
  return Promise.all(
    summaries.map((summary) =>
      repositoryWorkspaceGateway.getWorkspace(summary.workspace_id),
    ),
  );
}
