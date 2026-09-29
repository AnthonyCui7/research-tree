import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ProfileMenu, type AccountScreen } from "./ProfileMenu";
import { AccountScreens, useApiKeyLabel } from "../account/AccountScreens";
import { TreeCanvas } from "../tree/TreeCanvas";
import { CanvasErrorBoundary } from "../ui/CanvasErrorBoundary";
import { SearchOverlay } from "../search/SearchOverlay";
import { NodeInspector, inspectorLabel } from "../inspector/NodeInspector";
import { RightPanel, RIGHT_PANEL_DEFAULT_WIDTH } from "../panel/RightPanel";
import { NewWorkspacePage } from "../workspace/NewWorkspacePage";
import { WorkspaceNotice } from "../workspace/WorkspaceNotice";
import { WorkspaceHistory } from "../workspace/WorkspaceHistory";
import { DeleteWorkspaceDialog } from "../workspace/DeleteWorkspaceDialog";
import { NodeActionsMenu } from "../workspace/NodeActionsMenu";
import { Toast, ToastStack } from "../ui/Toast";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import { ClockIcon, TrashIcon } from "../ui/icons";
import { messageFrom } from "../../lib/apiError";
import { useAgentSession } from "../../data/useAgentSession";
import { useWorkspaceEditor } from "../../data/useWorkspaceEditor";
import type { BuildRun } from "../../data/useBuildRun";
import { isLocalSession, signOut, useSessionInfo } from "../../data/session";
import { PANEL_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { isRunActive } from "../../lib/pipelineStages";
import type { Route } from "../../app/App";
import type {
  SessionUser,
  TreeNodeId,
  TreeViewModel,
  WorkspaceDocument,
  WorkspaceEditOperation,
  WorkspaceSummary,
} from "../../lib/types";

export type UtilityPanel = "inspector" | "agent" | "history";

// The assistant renders replies with a markdown engine about as large as the
// rest of the app; it loads the first time the panel opens.
const WorkspaceAgent = lazy(() =>
  import("../workspace/WorkspaceAgent").then((module) => ({ default: module.WorkspaceAgent })),
);

// The shell only renders behind the session gate, so this is never shown; it
// keeps the account row's prop total while the store is mid-update.
const LOCAL_USER: SessionUser = {
  id: "local_user",
  email: "",
  name: "Local profile",
  avatar_url: null,
  is_admin: true,
  is_verified: true,
};

type AppShellProps = {
  status: "loading" | "ready" | "error";
  error: string | null;
  live: boolean;
  workspaces: WorkspaceSummary[];
  /** Null until the workspace list has arrived. */
  route: Route | null;
  activeSummary: WorkspaceSummary | null;
  tree: TreeViewModel | null;
  activeWorkspace: WorkspaceDocument | null;
  workspaceLoading: boolean;
  workspaceError: string | null;
  selectedNodeId: TreeNodeId | null;
  panel: UtilityPanel | null;
  sidebarCollapsed: boolean;
  build: BuildRun;
  onOpenWorkspace: (workspaceId: string) => void;
  onOpenHome: () => void;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onOpenPanel: (panel: UtilityPanel) => void;
  onClosePanel: () => void;
  onWorkspaceChanged: () => Promise<void>;
  onWorkspaceDeleted: (workspaceId: string) => Promise<void>;
  onToggleSidebar: () => void;
  onRefresh: () => void;
};

export function AppShell({
  status,
  error,
  live,
  workspaces,
  route,
  activeSummary,
  tree,
  activeWorkspace,
  workspaceLoading,
  workspaceError,
  selectedNodeId,
  panel,
  sidebarCollapsed,
  build,
  onOpenWorkspace,
  onOpenHome,
  onSelectNode,
  onOpenPanel,
  onClosePanel,
  onWorkspaceChanged,
  onWorkspaceDeleted,
  onToggleSidebar,
  onRefresh,
}: AppShellProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [accountAnchor, setAccountAnchor] = useState<MenuAnchor | null>(null);
  const [optionsMenu, setOptionsMenu] = useState<
    { workspace: WorkspaceSummary; anchor: MenuAnchor } | null
  >(null);
  // The id, not the summary: a delete that loses a version race refreshes the
  // list, and holding a snapshot meant the dialog kept confirming against the
  // hash that had just been superseded, so every retry conflicted again.
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const deleteTarget = workspaces.find((item) => item.workspace_id === deleteTargetId) ?? null;
  const [accountScreen, setAccountScreen] = useState<AccountScreen | null>(null);
  // The assistant opens at half the screen and remembers its own drag width;
  // the narrower inspector/history column keeps a separate one.
  const [agentPanelWidth, setAgentPanelWidth] = useState<number | null>(null);
  const [utilityPanelWidth, setUtilityPanelWidth] = useState(RIGHT_PANEL_DEFAULT_WIDTH);
  // A failed background refresh keeps the workspaces already on screen, so the
  // failure has nowhere else to appear. Dismissal is tracked by message, so a
  // later — different — failure still speaks up.
  const [dismissedError, setDismissedError] = useState<string | null>(null);
  // Sign-out is the one request whose failure leaves the reader signed in;
  // it is reported here, since the menu that asked for it has already closed.
  const [signOutError, setSignOutError] = useState<string | null>(null);
  // The actions menu for one card, opened from the canvas or the inspector.
  const [nodeActions, setNodeActions] = useState<{ nodeId: TreeNodeId; anchor: MenuAnchor } | null>(
    null,
  );
  const [renamingNodeId, setRenamingNodeId] = useState<TreeNodeId | null>(null);
  // A moved paper gets a new card id (its path and position changed), so the
  // selection follows the paper rather than the id once the tree reloads. The
  // version the move was sent against says when that reload has happened.
  const [followPaper, setFollowPaper] = useState<{ paperId: string; from: string } | null>(null);

  const home = route?.kind === "home";
  const activeWorkspaceId = activeSummary?.workspace_id ?? null;
  const selectedNode = tree && selectedNodeId ? (tree.nodesById[selectedNodeId] ?? null) : null;
  const activeRunning =
    build.run?.workspace_id === activeWorkspaceId && isRunActive(build.run);
  const refreshError = status !== "error" && error && error !== dismissedError ? error : null;
  // A build that stopped short is reported on the new-workspace page. Away
  // from it, one whose workspace is listed (a rebuild, or a build that failed
  // after its structure landed) is reported here instead; one that never
  // landed keeps a row in the sidebar that leads back to the page.
  const stoppedBuild =
    !home &&
    build.run &&
    !isRunActive(build.run) &&
    workspaces.some((workspace) => workspace.workspace_id === build.run?.workspace_id)
      ? build.run
      : null;
  const session = useAgentSession(
    activeWorkspace?.workspace_id ?? null,
    onWorkspaceChanged,
    build.watch,
  );
  const editor = useWorkspaceEditor(
    activeWorkspaceId,
    tree?.currentVersionHash ?? null,
    onWorkspaceChanged,
  );
  // The label beside "API keys" is reread when that screen closes: it is the
  // one place the key can change.
  const [apiKeyEpoch, setApiKeyEpoch] = useState(0);
  const apiKeyLabel = useApiKeyLabel(apiKeyEpoch);
  const sessionInfo = useSessionInfo();
  const user = sessionInfo?.user ?? LOCAL_USER;

  useEffect(() => {
    setRenamingNodeId(null);
  }, [selectedNodeId]);

  // The browser tab names what it holds, so a reader with several open can
  // tell them apart.
  const pageTitle = home ? "New workspace" : (tree?.title ?? activeSummary?.title ?? null);
  useEffect(() => {
    document.title = pageTitle ? `${pageTitle} · Research Tree` : "Research Tree";
    return () => {
      document.title = "Research Tree";
    };
  }, [pageTitle]);

  useEffect(() => {
    if (!followPaper || !tree || tree.currentVersionHash === followPaper.from) return;
    const moved = tree.nodes.find(
      (node) => node.kind === "paper" && node.paperId === followPaper.paperId,
    );
    if (moved) onSelectNode(moved.id);
    setFollowPaper(null);
  }, [followPaper, onSelectNode, tree]);

  function applyEdit(operations: WorkspaceEditOperation[]) {
    const move = operations.find((operation) => operation.op === "move");
    if (move && tree?.currentVersionHash) {
      setFollowPaper({ paperId: move.paper_id, from: tree.currentVersionHash });
    }
    void editor.apply(operations).then((result) => {
      // A move that did not land has no new card to follow. Left standing, the
      // next version change from anywhere would open the inspector on this
      // paper unasked.
      if (move && !result?.changed) setFollowPaper(null);
    });
  }

  // A refresh that succeeds re-arms the strip, so the same failure returning
  // after a good refresh is reported again rather than silently swallowed.
  useEffect(() => {
    if (!error) {
      setDismissedError(null);
    }
  }, [error]);

  useEffect(() => {
    if (!tree) {
      setSearchOpen(false);
    }
  }, [tree]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (tree && !home) setSearchOpen((open) => !open);
        return;
      }
      // One Escape closes one thing, outermost first. Anything layered above the
      // panel — search, a dialog, a menu — claims the key before it reaches here.
      if (event.key === "Escape" && panel) {
        onClosePanel();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [home, onClosePanel, panel, tree]);

  const historyReady = Boolean(activeWorkspace && tree?.currentVersionHash);
  const panelOpen =
    !home &&
    Boolean(
      (panel === "inspector" && selectedNode) ||
        (panel === "agent" && activeWorkspace) ||
        (panel === "history" && historyReady),
    );
  const panelPresence = useExitAnimation(panelOpen, PANEL_EXIT_MS);

  // What the panel was showing when it was told to close. The mode itself is
  // cleared immediately, but the panel is still on screen playing its exit, and
  // an empty box sliding out reads as a glitch.
  const lastPanelRef = useRef<UtilityPanel | null>(null);
  useEffect(() => {
    if (panelOpen && panel) {
      lastPanelRef.current = panel;
    }
  }, [panel, panelOpen]);
  const shownPanel = panel ?? lastPanelRef.current;

  function togglePanel(target: UtilityPanel) {
    if (panel === target) onClosePanel();
    else onOpenPanel(target);
  }

  let mainContent: ReactNode;
  if (status === "error") {
    mainContent = (
      <WorkspaceNotice
        title="Workspaces unavailable"
        detail={error ?? "Your workspaces could not be loaded. Refresh and try again."}
        tone="error"
        actionLabel="Retry"
        onAction={onRefresh}
      />
    );
  } else if (home) {
    mainContent = (
      <NewWorkspacePage
        build={build}
        onOpenWorkspace={onOpenWorkspace}
        onOpenApiKeys={() => setAccountScreen("api-keys")}
      />
    );
  } else if (!route || !activeSummary) {
    mainContent = <WorkspaceNotice title="Loading" tone="loading" />;
  } else {
    mainContent = (
      <>
        <TopBar
          workspaceTitle={tree?.title ?? activeSummary.title}
          branchCount={tree?.branchCount ?? null}
          paperCount={tree?.paperCount ?? null}
          onOpenSearch={() => setSearchOpen(true)}
          searchDisabled={!tree}
          onToggleHistory={() => togglePanel("history")}
          historyActive={panel === "history"}
          historyDisabled={!historyReady}
          onToggleAssistant={() => togglePanel("agent")}
          assistantActive={panel === "agent"}
          assistantDisabled={!activeWorkspace || activeRunning}
          assistantDisabledReason={activeRunning ? "Wait for the build to finish" : undefined}
        />
        <div className="relative flex min-h-0 flex-1">
          <section className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
            {!tree && workspaceLoading ? <WorkspaceNotice title="Loading workspace" tone="loading" /> : null}
            {!tree && !workspaceLoading && workspaceError ? (
              <WorkspaceNotice
                title="Workspace failed to load"
                detail={workspaceError}
                tone="error"
                actionLabel="Retry"
                onAction={onRefresh}
              />
            ) : null}
            {tree ? (
              <CanvasErrorBoundary
                fallback={
                  <WorkspaceNotice
                    title="Workspace failed to load"
                    detail="This workspace's saved contents are damaged, so its tree cannot be displayed."
                    tone="error"
                    actionLabel="Retry"
                    onAction={onRefresh}
                  />
                }
              >
                <TreeCanvas
                  tree={tree}
                  workspace={activeWorkspace}
                  selectedNodeId={selectedNodeId}
                  onSelectNode={onSelectNode}
                  onOpenNodeActions={(nodeId, point) => {
                    onSelectNode(nodeId);
                    setNodeActions({ nodeId, anchor: { x: point.x, y: point.y, align: "left" } });
                  }}
                />
              </CanvasErrorBoundary>
            ) : null}
          </section>

          {panelPresence.present && shownPanel ? (
            <RightPanel
              label={
                shownPanel === "agent"
                  ? "Workspace assistant"
                  : shownPanel === "history"
                    ? "Version history"
                    : selectedNode
                      ? inspectorLabel(selectedNode)
                      : "Details"
              }
              closing={panelPresence.closing}
              width={
                shownPanel === "agent"
                  ? (agentPanelWidth ?? Math.round(window.innerWidth / 2))
                  : utilityPanelWidth
              }
              onWidth={shownPanel === "agent" ? setAgentPanelWidth : setUtilityPanelWidth}
              sidebarCollapsed={sidebarCollapsed}
            >
              {shownPanel === "inspector" && selectedNode && tree ? (
                <NodeInspector
                  node={selectedNode}
                  tree={tree}
                  updatedAt={activeSummary.updated_at}
                  onSelectNode={onSelectNode}
                  onClose={onClosePanel}
                  onOpenAssistant={() => onOpenPanel("agent")}
                  onOpenActions={(trigger) =>
                    setNodeActions({
                      nodeId: selectedNode.id,
                      anchor: anchorFromEvent(trigger, "right"),
                    })
                  }
                  actionsOpen={nodeActions?.nodeId === selectedNode.id}
                  renaming={renamingNodeId === selectedNode.id}
                  onRenameSubmit={async (label) => {
                    if (selectedNode.kind !== "branch") return;
                    const result = await editor.apply([
                      {
                        op: "set",
                        entity_type: "branch",
                        branch_id: selectedNode.branchNodeId,
                        field: "label",
                        value: label,
                      },
                    ]);
                    if (result) setRenamingNodeId(null);
                  }}
                  onRenameCancel={() => setRenamingNodeId(null)}
                />
              ) : null}
              {shownPanel === "agent" && activeWorkspace ? (
                <Suspense fallback={<WorkspaceNotice title="Loading" tone="loading" />}>
                  <WorkspaceAgent
                    session={session}
                    tree={tree}
                    onClose={onClosePanel}
                    onOpenApiKeys={() => setAccountScreen("api-keys")}
                  />
                </Suspense>
              ) : null}
              {shownPanel === "history" && activeWorkspace && tree?.currentVersionHash ? (
                <WorkspaceHistory
                  key={`history:${activeWorkspace.workspace_id}`}
                  workspaceId={activeWorkspace.workspace_id}
                  currentVersionHash={tree.currentVersionHash}
                  onClose={onClosePanel}
                  onChanged={onWorkspaceChanged}
                />
              ) : null}
            </RightPanel>
          ) : null}
        </div>
      </>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-surface">
      <Sidebar
        collapsed={sidebarCollapsed}
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        homeActive={home}
        buildingRun={build.run}
        live={live}
        user={user}
        accountDetail={sessionInfo && isLocalSession(sessionInfo) ? "Runs without accounts" : user.email}
        accountMenuOpen={accountAnchor !== null}
        onToggle={onToggleSidebar}
        onNewWorkspace={onOpenHome}
        onSelectWorkspace={onOpenWorkspace}
        onOpenOptions={(workspace, trigger) =>
          setOptionsMenu({ workspace, anchor: anchorFromEvent(trigger, "left") })
        }
        optionsOpenFor={optionsMenu?.workspace.workspace_id ?? null}
        onOpenAccount={(trigger) =>
          setAccountAnchor((current) => (current ? null : anchorFromEvent(trigger, "left", true)))
        }
      />

      <main className="relative flex min-w-0 flex-1 flex-col" aria-label="Research workspace">
        {mainContent}

        {refreshError || signOutError || stoppedBuild || editor.notice ? (
          <ToastStack>
            {refreshError ? (
              <Toast
                tone="error"
                onDismiss={() => setDismissedError(refreshError)}
                dismissLabel="Dismiss workspace refresh error"
              >
                {refreshError}
              </Toast>
            ) : null}
            {signOutError ? (
              <Toast
                tone="error"
                onDismiss={() => setSignOutError(null)}
                dismissLabel="Dismiss sign-out error"
              >
                {signOutError}
              </Toast>
            ) : null}
            {stoppedBuild ? (
              <Toast tone="error" onDismiss={build.dismiss} dismissLabel="Dismiss build notice">
                {stoppedBuild.status === "cancelled"
                  ? `The build of “${stoppedBuild.topic}” was cancelled.`
                  : `The build of “${stoppedBuild.topic}” failed. ${stoppedBuild.error ?? ""}`.trim()}
              </Toast>
            ) : null}
            {editor.notice ? (
              <Toast
                key={editor.notice.text}
                tone={editor.notice.tone}
                onDismiss={editor.dismissNotice}
                dismissLabel="Dismiss"
                action={
                  editor.notice.undo
                    ? { label: "Undo", onClick: editor.notice.undo, disabled: editor.busy }
                    : undefined
                }
              >
                {editor.notice.text}
              </Toast>
            ) : null}
          </ToastStack>
        ) : null}
      </main>

      {searchOpen && tree ? (
        <SearchOverlay
          tree={tree}
          onSelectNode={onSelectNode}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}

      {accountAnchor && sessionInfo ? (
        <ProfileMenu
          anchor={accountAnchor}
          session={sessionInfo}
          onClose={() => setAccountAnchor(null)}
          onOpenScreen={setAccountScreen}
          onSignOut={() => {
            setAccountAnchor(null);
            setSignOutError(null);
            signOut().catch((signOutFailure: unknown) => {
              setSignOutError(
                `Sign-out did not complete, so you are still signed in. ${messageFrom(signOutFailure)}`,
              );
            });
          }}
          apiKeyLabel={apiKeyLabel}
        />
      ) : null}

      <AccountScreens
        screen={accountScreen}
        onClose={() => {
          if (accountScreen === "api-keys") setApiKeyEpoch((epoch) => epoch + 1);
          setAccountScreen(null);
        }}
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
        live={live}
        onRefresh={onRefresh}
      />

      {optionsMenu ? (
        <PopoverMenu
          anchor={optionsMenu.anchor}
          onClose={() => setOptionsMenu(null)}
          label={`Options for ${optionsMenu.workspace.title}`}
        >
          <MenuSection>
            <MenuItem
              icon={<ClockIcon className="h-[14px] w-[14px]" />}
              onClick={() => {
                onOpenWorkspace(optionsMenu.workspace.workspace_id);
                onOpenPanel("history");
              }}
            >
              Version history
            </MenuItem>
          </MenuSection>
          <MenuSection>
            <MenuItem
              tone="danger"
              icon={<TrashIcon className="h-[14px] w-[14px]" />}
              onClick={() => setDeleteTargetId(optionsMenu.workspace.workspace_id)}
            >
              Delete workspace…
            </MenuItem>
          </MenuSection>
        </PopoverMenu>
      ) : null}

      {nodeActions && tree ? (
        <NodeActionsFor
          tree={tree}
          nodeId={nodeActions.nodeId}
          anchor={nodeActions.anchor}
          disabled={activeRunning || editor.busy}
          onClose={() => setNodeActions(null)}
          onRename={(branchId) => {
            setRenamingNodeId(branchId);
            onOpenPanel("inspector");
          }}
          onApply={applyEdit}
        />
      ) : null}

      {deleteTarget ? (
        <DeleteWorkspaceDialog
          workspace={deleteTarget}
          onCancel={() => setDeleteTargetId(null)}
          onChanged={onWorkspaceChanged}
          onDeleted={async () => {
            setDeleteTargetId(null);
            await onWorkspaceDeleted(deleteTarget.workspace_id);
          }}
        />
      ) : null}
    </div>
  );
}

/** The actions menu, once the card it was opened for is known to be a branch or paper. */
function NodeActionsFor({
  tree,
  nodeId,
  anchor,
  disabled,
  onClose,
  onRename,
  onApply,
}: {
  tree: TreeViewModel;
  nodeId: TreeNodeId;
  anchor: MenuAnchor;
  disabled: boolean;
  onClose: () => void;
  onRename: (branchId: string) => void;
  onApply: (operations: WorkspaceEditOperation[]) => void;
}) {
  const node = tree.nodesById[nodeId];
  const gone = !node || node.kind === "root";
  // The card can vanish under an open menu — its own removal, or a reload
  // that gave it a new id. Closing here keeps the menu from reappearing when
  // the card comes back, as it does after an undo.
  useEffect(() => {
    if (gone) onClose();
  }, [gone, onClose]);
  if (gone) return null;
  return (
    <NodeActionsMenu
      node={node}
      tree={tree}
      anchor={anchor}
      disabled={disabled}
      onClose={onClose}
      onRename={onRename}
      onApply={onApply}
    />
  );
}
