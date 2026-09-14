import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell, type UtilityPanel } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { useActiveWorkspace } from "../data/useActiveWorkspace";
import { repositoryWorkspaceGateway } from "../data/workspaceApi";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import { isRunActive } from "../lib/pipelineStages";
import type { PipelineRun, TreeNodeId, TreeViewModel } from "../lib/types";

const SIDEBAR_COLLAPSED_STORAGE_KEY = "research-tree.sidebar-collapsed";

/** What a workspace was left showing, so returning to it resumes rather than resets. */
type WorkspaceView = { panel: UtilityPanel | null; selectedNodeId: TreeNodeId | null };

export function App() {
  const { status, workspaces, error, live, refresh } = useWorkspaceCollection();
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);
  const [panel, setPanel] = useState<UtilityPanel | null>(null);
  const [creatorOpen, setCreatorOpen] = useState(false);
  const [creatorTopic, setCreatorTopic] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
    } catch {
      // A browser set to block site data throws here rather than returning
      // null, and this runs in a state initializer above every error boundary,
      // so an unguarded read left the whole page blank. The sidebar opens.
      return false;
    }
  });
  const [buildingRun, setBuildingRun] = useState<PipelineRun | null>(null);

  // A build lives on the server; the page only watches it. A page reloaded
  // mid-build has no other way to find the build again: the workspace is not
  // listed until its first version lands, so the sidebar would show nothing
  // and offer a second build. Asked once, when the collection first loads.
  const recoveredRunsRef = useRef(false);
  useEffect(() => {
    if (status !== "ready" || recoveredRunsRef.current) return;
    recoveredRunsRef.current = true;
    let cancelled = false;
    repositoryWorkspaceGateway
      .listActivePipelineRuns()
      .then((runs) => {
        const newest = runs.find(isRunActive) ?? null;
        if (!cancelled && newest) setBuildingRun((current) => current ?? newest);
      })
      // A build that cannot be recovered is a build the sidebar does not
      // show until it lands; nothing else is lost.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [status]);

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

  // The list is re-sorted by recency on every change and the fallback above
  // reads its head, so what is shown is pinned by id as soon as it is shown:
  // a build finishing elsewhere, or an edit in another tab, must not switch
  // the workspace under the reader. Only a workspace that is gone falls through.
  useEffect(() => {
    const first = workspaces[0];
    if (!first || workspaces.some((workspace) => workspace.workspace_id === selectedWorkspaceId)) {
      return;
    }
    setSelectedWorkspaceId(first.workspace_id);
  }, [selectedWorkspaceId, workspaces]);

  const {
    workspace: activeWorkspace,
    loading: workspaceLoading,
    error: workspaceError,
  } = useActiveWorkspace(activeSummary);

  // A document the canvas cannot lay out must not take the rest of the app with
  // it. This runs during render, and the sidebar, the top bar and every way out
  // to another workspace live above it, so a throw here used to leave a blank
  // page that a reload reproduced.
  const [tree, treeError] = useMemo<[TreeViewModel | null, string | null]>(() => {
    if (!activeWorkspace) {
      return [null, null];
    }
    try {
      return [normalizeWorkspaceForTree(activeWorkspace), null];
    } catch (error) {
      console.error("workspace could not be laid out", error);
      return [null, "This workspace's saved contents are damaged, so its tree cannot be displayed."];
    }
  }, [activeWorkspace]);

  const activeWorkspaceId = activeSummary?.workspace_id ?? null;

  // The panel and the selected card belong to the workspace they were opened
  // from, so stepping away and back resumes the reading rather than restarting
  // it. The render that switches workspaces is skipped: what is on screen at
  // that moment still belongs to the workspace being left.
  const viewsRef = useRef(new Map<string, WorkspaceView>());
  const shownWorkspaceRef = useRef<string | null>(null);
  useEffect(() => {
    if (shownWorkspaceRef.current !== activeWorkspaceId) {
      shownWorkspaceRef.current = activeWorkspaceId;
      return;
    }
    if (activeWorkspaceId) {
      viewsRef.current.set(activeWorkspaceId, { panel, selectedNodeId });
    }
  }, [activeWorkspaceId, panel, selectedNodeId]);

  function selectWorkspace(workspaceId: string) {
    const view = viewsRef.current.get(workspaceId);
    setSelectedWorkspaceId(workspaceId);
    setPanel(view?.panel ?? null);
    setSelectedNodeId(view?.selectedNodeId ?? null);
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
      // Called once when the workspace becomes openable and again when the
      // build finishes. A reader already inside it keeps their panel and
      // selection the second time; only arriving resets the view.
      if (shownWorkspaceRef.current !== workspaceId) {
        setPanel(null);
        setSelectedNodeId(null);
      }
      setSelectedWorkspaceId(workspaceId);
      setCreatorOpen(false);
      setCreatorTopic("");
      setBuildingRun(isRunActive(run) ? run : null);
    },
    [refresh],
  );

  // Any workspace can be deleted from the sidebar, not only the one on screen:
  // its remembered view goes, and the reader's place is disturbed only when it
  // was theirs. The list is refreshed first, so the canvas moves straight from
  // a deleted workspace to the next one rather than through a fetch of one
  // that is already gone.
  const handleDeleted = useCallback(
    async (workspaceId: string) => {
      viewsRef.current.delete(workspaceId);
      await refresh();
      if (workspaceId === activeWorkspaceId) {
        setPanel(null);
        setSelectedNodeId(null);
      }
    },
    [activeWorkspaceId, refresh],
  );

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
      workspaceError={workspaceError ?? treeError}
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
