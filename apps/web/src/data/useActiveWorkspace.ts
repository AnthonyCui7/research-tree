import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "./workspaceApi";
import { messageFrom } from "../lib/apiError";
import type { WorkspaceDocument, WorkspaceSummary } from "../lib/types";

type ActiveWorkspaceState = {
  workspace: WorkspaceDocument | null;
  loading: boolean;
  error: string | null;
};

/**
 * Fetches the full document (and version list) for the selected workspace
 * only. Keyed on the summary's version hash, so the collection SSE stream
 * refreshing summaries is what triggers a refetch after any change — no
 * per-document polling and no whole-collection fan-out.
 */
export function useActiveWorkspace(summary: WorkspaceSummary | null): ActiveWorkspaceState {
  const [state, setState] = useState<ActiveWorkspaceState>({
    workspace: null,
    loading: false,
    error: null,
  });
  const workspaceId = summary?.workspace_id ?? null;
  const versionHash = summary?.workspace_version_hash ?? null;

  useEffect(() => {
    if (!workspaceId) {
      setState({ workspace: null, loading: false, error: null });
      return;
    }
    let active = true;
    setState((current) => ({
      workspace: current.workspace?.workspace_id === workspaceId ? current.workspace : null,
      loading: true,
      error: null,
    }));
    void (async () => {
      try {
        const [response, versions] = await Promise.all([
          repositoryWorkspaceGateway.getWorkspace(workspaceId),
          repositoryWorkspaceGateway.getWorkspaceVersions(workspaceId),
        ]);
        if (!active) {
          return;
        }
        setState({
          workspace: {
            ...response.workspace,
            current_workspace_version_hash: response.workspace_version_hash,
            workspace_versions: versions,
          },
          loading: false,
          error: null,
        });
      } catch (error) {
        // Reporting the failure keeps a broken fetch from rendering as the
        // "no workspaces yet" empty state, which reads as data loss.
        if (active) {
          setState({ workspace: null, loading: false, error: messageFrom(error) });
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [workspaceId, versionHash]);

  return state;
}
