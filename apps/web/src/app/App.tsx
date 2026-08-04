import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { useActiveWorkspace } from "../data/useActiveWorkspace";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import type { PipelineRun, TreeNodeId } from "../lib/types";

const SIDEBAR_COLLAPSED_STORAGE_KEY = "research-tree.sidebar-collapsed";

export function App() {
  const { status, workspaces, error, live, refresh } = useWorkspaceCollection();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [utilityPanel, setUtilityPanel] = useState<"history" | "agent" | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true",
  );
  const [buildingRun, setBuildingRun] = useState<PipelineRun | null>(null);

  const activeSummary = useMemo(() => {
    if (workspaces.length === 0) {
      return null;
    }
    return (
      workspaces.find((workspace) => workspace.workspace_id === selectedWorkspaceId) ??
      workspaces[0] ?? null
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

  // Persisting outside the updater keeps it pure under StrictMode's double
  // invocation, and storage that refuses writes (private browsing) costs the
  // preference, not the app.
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(sidebarCollapsed));
    } catch {
      // The sidebar simply reopens expanded next session.
    }
  }, [sidebarCollapsed]);

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => !collapsed);
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
      live={live}
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
