import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AppShell, type UtilityPanel } from "../components/layout/AppShell";
import { useWorkspaceCollection } from "../data/useWorkspaceCollection";
import { useActiveWorkspace } from "../data/useActiveWorkspace";
import { useBuildRun } from "../data/useBuildRun";
import { normalizeWorkspaceForTree } from "../lib/workspaceAdapter";
import type { TreeNodeId, TreeViewModel } from "../lib/types";

const SIDEBAR_COLLAPSED_STORAGE_KEY = "research-tree.sidebar-collapsed";

/** Below this width an open sidebar covers the canvas rather than sitting beside it. */
const NARROW_VIEWPORT = 900;

/** What the main area shows: the page for starting a workspace, or one workspace. */
export type Route = { kind: "home" } | { kind: "workspace"; workspaceId: string };

/** What a workspace was left showing, so returning to it resumes rather than resets. */
type WorkspaceView = { panel: UtilityPanel | null; selectedNodeId: TreeNodeId | null };

export function App() {
  const { status, workspaces, error, live, refresh } = useWorkspaceCollection();
  // Null until the workspace list first arrives and says where to start.
  const [route, setRoute] = useState<Route | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<TreeNodeId | null>(null);
  const [panel, setPanel] = useState<UtilityPanel | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);

  // Arriving opens the most recent workspace, or the new-workspace page when
  // there is none. After that the route is pinned by id: the list re-sorts on
  // every change, and a build finishing elsewhere must not switch the
  // workspace under the reader. Only a workspace that is gone falls through.
  useEffect(() => {
    if (status !== "ready" || route?.kind === "home") return;
    if (route && workspaces.some((workspace) => workspace.workspace_id === route.workspaceId)) return;
    const first = workspaces[0];
    setRoute(first ? { kind: "workspace", workspaceId: first.workspace_id } : { kind: "home" });
  }, [route, status, workspaces]);

  const activeSummary = useMemo(() => {
    if (route?.kind !== "workspace") return null;
    return workspaces.find((workspace) => workspace.workspace_id === route.workspaceId) ?? null;
  }, [route, workspaces]);

  const {
    workspace: activeWorkspace,
    loading: workspaceLoading,
    error: workspaceError,
  } = useActiveWorkspace(activeSummary);

  // A document the canvas cannot lay out must not take the rest of the app with
  // it. This runs during render, and the sidebar and every way out to another
  // workspace live above it, so a throw here used to leave a blank page that a
  // reload reproduced.
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

  const openWorkspace = useCallback((workspaceId: string) => {
    const view = viewsRef.current.get(workspaceId);
    setRoute({ kind: "workspace", workspaceId });
    setPanel(view?.panel ?? null);
    setSelectedNodeId(view?.selectedNodeId ?? null);
  }, []);

  const openHome = useCallback(() => {
    setRoute({ kind: "home" });
    setPanel(null);
    setSelectedNodeId(null);
  }, []);

  // A build that becomes openable takes the reader into it only when they are
  // on the page watching it; anyone who has moved on finds it in the sidebar.
  const routeRef = useRef(route);
  useEffect(() => {
    routeRef.current = route;
  }, [route]);
  const handleBuildReady = useCallback(
    async (workspaceId: string) => {
      await refresh();
      if (routeRef.current?.kind === "home") openWorkspace(workspaceId);
    },
    [openWorkspace, refresh],
  );
  const build = useBuildRun(handleBuildReady);

  // Selecting a node is what opens the inspector; the selection outlives the
  // panel, so the card stays marked after the panel is dismissed.
  const selectNode = useCallback((nodeId: TreeNodeId) => {
    setSelectedNodeId(nodeId);
    setPanel("inspector");
  }, []);

  // Persisting outside the updater keeps it pure under StrictMode's double
  // invocation, and storage that refuses writes (private browsing) costs the
  // preference, not the app.
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_STORAGE_KEY, String(sidebarCollapsed));
    } catch {
      // The sidebar simply opens in its default state next session.
    }
  }, [sidebarCollapsed]);

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

  return (
    <AppShell
      status={status}
      error={error}
      live={live}
      workspaces={workspaces}
      route={route}
      activeSummary={activeSummary}
      tree={tree}
      activeWorkspace={activeWorkspace}
      workspaceLoading={workspaceLoading}
      workspaceError={workspaceError ?? treeError}
      selectedNodeId={selectedNodeId}
      panel={panel}
      sidebarCollapsed={sidebarCollapsed}
      build={build}
      onOpenWorkspace={openWorkspace}
      onOpenHome={openHome}
      onSelectNode={selectNode}
      onOpenPanel={setPanel}
      onClosePanel={() => setPanel(null)}
      onWorkspaceChanged={refresh}
      onWorkspaceDeleted={handleDeleted}
      onToggleSidebar={() => setSidebarCollapsed((collapsed) => !collapsed)}
      onRefresh={() => void refresh()}
    />
  );
}

/**
 * The stored preference, or collapsed on a narrow screen where an open sidebar
 * would cover the canvas on arrival.
 */
function readSidebarCollapsed(): boolean {
  try {
    const stored = window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY);
    if (stored !== null) return stored === "true";
  } catch {
    // A browser set to block site data throws here rather than returning
    // null, and this runs in a state initializer above every error boundary,
    // so an unguarded read left the whole page blank.
  }
  return window.innerWidth < NARROW_VIEWPORT;
}
