import { useMemo, useState } from "react";
import { AppShell } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import type { TreeNodeId } from "../lib/types";

export function App() {
  const { status, workspaces, error } = useWorkspaceCollection();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);

  const activeWorkspace = useMemo(() => {
    if (workspaces.length === 0) {
      return null;
    }
    return (
      workspaces.find((workspace) => workspace.workspace_id === selectedWorkspaceId) ??
      workspaces[0]
    );
  }, [selectedWorkspaceId, workspaces]);

  const tree = useMemo(() => {
    if (!activeWorkspace) {
      return null;
    }
    return normalizeWorkspaceForTree(activeWorkspace);
  }, [activeWorkspace]);

  const activeWorkspaceId = activeWorkspace?.workspace_id ?? null;

  function selectWorkspace(workspaceId: string) {
    setSelectedWorkspaceId(workspaceId);
    setSelectedNodeId(null);
  }

  return (
    <AppShell
      status={status}
      error={error}
      workspaces={workspaces}
      activeWorkspaceId={activeWorkspaceId}
      tree={tree}
      selectedNodeId={selectedNodeId}
      onSelectWorkspace={selectWorkspace}
      onSelectNode={setSelectedNodeId}
      onCloseInspector={() => setSelectedNodeId(null)}
    />
  );
}
