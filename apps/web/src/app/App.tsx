import { useCallback, useEffect, useMemo, useState } from "react";
import { AppShell, type UtilityPanel } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { useActiveWorkspace } from "../data/useActiveWorkspace";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import type { PipelineRun, TreeNodeId } from "../lib/types";

const SIDEBAR_COLLAPSED_STORAGE_KEY = "research-tree.sidebar-collapsed";

export function App() {
  const { status, workspaces, error, live, refresh } = useWorkspaceCollection();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);
  const [panel, setPanel] = useState<UtilityPanel | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [creatorTopic, setCreatorTopic] = useState("");
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
      workspaces[0] ??
      null
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

  function selectWorkspace(workspaceId: string) {
    setSelectedWorkspaceId(workspaceId);
    setSelectedNodeId(null);
    setPanel(null);
  }

  // Selecting a node is what opens the inspector; the selection outlives the
  // panel, so the card stays marked after the panel is dismissed.
  function selectNode(nodeId: TreeNodeId) {
    setSelectedNodeId(nodeId);
    setPanel("inspector");
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

  const openCreator = useCallback((topic = "") => {
    setCreatorTopic(topic);
    setCreatorOpen(true);
  }, []);

  const handleCreated = useCallback(
    async (workspaceId: string, run: PipelineRun) => {
      await refresh();
      setSelectedWorkspaceId(workspaceId);
      setCreatorOpen(false);
      setCreatorTopic("");
      setBuildingRun(run.status === "queued" || run.status === "running" ? run : null);
    },
    [refresh],
  );

  const handleDeleted = useCallback(async () => {
    setSelectedWorkspaceId(null);
    setSelectedNodeId(null);
    setPanel(null);
    await refresh();
  }, [refresh]);

  const handlePipelineFinished = useCallback((runId: string) => {
    setBuildingRun((current) => (current?.run_id === runId ? null : current));
  }, []);

  return (
    <AppShell
      status={status}
      error={error}
      live={live}
      workspaces={workspaces}
      activeSummary={activeSummary}
      tree={tree}
      activeWorkspace={activeWorkspace}
      workspaceLoading={workspaceLoading}
      workspaceError={workspaceError}
      selectedNodeId={selectedNodeId}
      panel={panel}
      creatorOpen={creatorOpen}
      creatorTopic={creatorTopic}
      sidebarCollapsed={sidebarCollapsed}
      buildingRun={buildingRun}
      onSelectWorkspace={selectWorkspace}
      onSelectNode={selectNode}
      onOpenPanel={setPanel}
      onClosePanel={() => setPanel(null)}
      onOpenCreator={openCreator}
      onCloseCreator={() => setCreatorOpen(false)}
      onCreated={handleCreated}
      onWorkspaceChanged={refresh}
      onWorkspaceDeleted={handleDeleted}
      onToggleSidebar={() => setSidebarCollapsed((collapsed) => !collapsed)}
      onRefresh={() => void refresh()}
      onPipelineStarted={setBuildingRun}
      onPipelineFinished={handlePipelineFinished}
    />
  );
}
