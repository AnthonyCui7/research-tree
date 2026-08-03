import { useCallback, useMemo, useState } from "react";
import { AppShell } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { useActiveWorkspace } from "../data/useActiveWorkspace";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import type { PipelineRun, TreeNodeId } from "../lib/types";

export function App() {
  const { status, workspaces, error, refresh } = useWorkspaceCollection();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [utilityPanel, setUtilityPanel] = useState<"history" | "agent" | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem("research-tree.sidebar-collapsed") === "true",
  );
  const [buildingRun, setBuildingRun] = useState<PipelineRun | null>(null);

  const activeSummary = useMemo(() => {
    if (workspaces.length === 0) {
      return null;
    }
    return (
      workspaces.find((workspace) => workspace.workspace_id === selectedWorkspaceId) ??
      workspaces[0]
    );
  }, [selectedWorkspaceId, workspaces]);

  const {
    workspace: activeWorkspace,
    loading: workspaceLoading,
    error: workspaceError,
  } = useActiveWorkspace(activeSummary);

  const tree = useMemo(() => {
    if (!activeWorkspace) {
      return null;
    }
    return normalizeWorkspaceForTree(activeWorkspace);
  }, [activeWorkspace]);

  const activeWorkspaceId = activeSummary?.workspace_id ?? null;

  function selectWorkspace(workspaceId: string) {
    setSelectedWorkspaceId(workspaceId);
    setSelectedNodeId(null);
    setUtilityPanel(null);
  }

  function selectNode(nodeId: TreeNodeId) {
    setSelectedNodeId(nodeId);
    setUtilityPanel(null);
  }

  function openUtility(panel: "history" | "agent" | null) {
    setSelectedNodeId(null);
    setUtilityPanel(panel);
  }

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      const next = !collapsed;
      window.localStorage.setItem("research-tree.sidebar-collapsed", String(next));
      return next;
    });
  }

  const handleCreated = useCallback(async (workspaceId: string, run: PipelineRun) => {
    await refresh();
    setSelectedWorkspaceId(workspaceId);
    setCreatorOpen(false);
    setBuildingRun(run.status === "queued" || run.status === "running" ? run : null);
  }, [refresh]);

  const handleDeleted = useCallback(async () => {
    setSelectedWorkspaceId(null);
    setSelectedNodeId(null);
    setUtilityPanel(null);
    await refresh();
  }, [refresh]);

  const handlePipelineFinished = useCallback((runId: string) => {
    setBuildingRun((current) => current?.run_id === runId ? null : current);
  }, []);

  return (
    <AppShell
      status={status}
      error={error}
      workspaces={workspaces}
      activeWorkspaceId={activeWorkspaceId}
      tree={tree}
      activeWorkspace={activeWorkspace}
      workspaceLoading={workspaceLoading}
      workspaceError={workspaceError}
      onRefresh={() => void refresh()}
      selectedNodeId={selectedNodeId}
      onSelectWorkspace={selectWorkspace}
      onSelectNode={selectNode}
      onCloseInspector={() => setSelectedNodeId(null)}
      creatorOpen={creatorOpen}
      utilityPanel={utilityPanel}
      sidebarCollapsed={sidebarCollapsed}
      buildingRun={buildingRun}
      onOpenCreator={() => setCreatorOpen(true)}
      onCloseCreator={() => setCreatorOpen(false)}
      onCreated={handleCreated}
      onOpenUtility={openUtility}
      onCloseUtility={() => setUtilityPanel(null)}
      onWorkspaceChanged={refresh}
      onWorkspaceDeleted={handleDeleted}
      onToggleSidebar={toggleSidebar}
      onResumeBuild={() => setCreatorOpen(true)}
      onPipelineStarted={setBuildingRun}
      onPipelineFinished={handlePipelineFinished}
    />
  );
}
