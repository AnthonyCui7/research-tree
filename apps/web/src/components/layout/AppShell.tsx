import { useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ProfileMenu, type AccountScreen } from "./ProfileMenu";
import { AccountScreens, useApiKeyLabel } from "../account/AccountScreens";
import { TreeCanvas } from "../tree/TreeCanvas";
import { CanvasErrorBoundary } from "../ui/CanvasErrorBoundary";
import { SearchOverlay } from "../search/SearchOverlay";
import { NodeInspector, inspectorLabel } from "../inspector/NodeInspector";
import { RightPanel, RIGHT_PANEL_DEFAULT_WIDTH } from "../panel/RightPanel";
import { WorkspaceEmptyState, WorkspaceNotice } from "../workspace/WorkspaceEmptyState";
import { WorkspaceCreator } from "../workspace/WorkspaceCreator";
import { WorkspaceHistory } from "../workspace/WorkspaceHistory";
import { WorkspaceAgent } from "../workspace/WorkspaceAgent";
import { DeleteWorkspaceDialog } from "../workspace/DeleteWorkspaceDialog";
import { NodeActionsMenu } from "../workspace/NodeActionsMenu";
import { Toast, ToastStack } from "../ui/Toast";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import { ClockIcon, TrashIcon } from "../ui/icons";
import { cx } from "../../lib/cx";
import { messageFrom } from "../../lib/apiError";
import { useAgentSession } from "../../data/useAgentSession";
import { useWorkspaceEditor } from "../../data/useWorkspaceEditor";
import { signOut, useSessionInfo } from "../../data/session";
import type { SessionUser } from "../../lib/types";

// The shell only renders behind the session gate, so this is never shown; it
// keeps the top bar's prop total when the store is mid-update.
const LOCAL_USER: SessionUser = {
  id: "local_user",
  email: "",
  name: "Local profile",
  avatar_url: null,
  is_admin: true,
  is_verified: true,
};
import { PANEL_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { isRunActive } from "../../lib/pipelineStages";
import type {
  PipelineRun,
  TreeNodeId,
  TreeViewModel,
  WorkspaceDocument,
  WorkspaceEditOperation,
  WorkspaceSummary,
} from "../../lib/types";

export type UtilityPanel = "inspector" | "agent" | "history";

type AppShellProps = {
  status: "loading" | "ready" | "error";
  error: string | null;
  live: boolean;
  workspaces: WorkspaceSummary[];
  activeSummary: WorkspaceSummary | null;
  tree: TreeViewModel | null;
  activeWorkspace: WorkspaceDocument | null;
  workspaceLoading: boolean;
  workspaceError: string | null;
  selectedNodeId: TreeNodeId | null;
  panel: UtilityPanel | null;
  creatorOpen: boolean;
  creatorTopic: string;
  sidebarCollapsed: boolean;
  buildingRun: PipelineRun | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onOpenPanel: (panel: UtilityPanel) => void;
  onClosePanel: () => void;
  onOpenCreator: (topic?: string) => void;
  onCloseCreator: () => void;
  onCreated: (workspaceId: string, run: PipelineRun) => Promise<void>;
  onWorkspaceChanged: () => Promise<void>;
  onWorkspaceDeleted: (workspaceId: string) => Promise<void>;
  onToggleSidebar: () => void;
  onRefresh: () => void;
  onPipelineStarted: (run: PipelineRun) => void;
  onPipelineFinished: (runId: string) => void;
};

export function AppShell({
  status,
  error,
  live,
  workspaces,
  activeSummary,
  tree,
  activeWorkspace,
  workspaceLoading,
  workspaceError,
  selectedNodeId,
  panel,
  creatorOpen,
  creatorTopic,
  sidebarCollapsed,
  buildingRun,
  onSelectWorkspace,
  onSelectNode,
  onOpenPanel,
  onClosePanel,
  onOpenCreator,
  onCloseCreator,
  onCreated,
  onWorkspaceChanged,
  onWorkspaceDeleted,
  onToggleSidebar,
  onRefresh,
  onPipelineStarted,
  onPipelineFinished,
}: AppShellProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  const [profileAnchor, setProfileAnchor] = useState<MenuAnchor | null>(null);
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

  const activeWorkspaceId = activeSummary?.workspace_id ?? null;
  const selectedNode = tree && selectedNodeId ? (tree.nodesById[selectedNodeId] ?? null) : null;
  const activeRunning =
    buildingRun?.workspace_id === activeWorkspaceId && isRunActive(buildingRun);
  const refreshError = status !== "error" && error && error !== dismissedError ? error : null;
  const session = useAgentSession(
    activeWorkspace?.workspace_id ?? null,
    onWorkspaceChanged,
    onPipelineStarted,
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

  useEffect(() => {
    setRenamingNodeId(null);
  }, [selectedNodeId]);

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
        if (tree) setSearchOpen((open) => !open);
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
  }, [onClosePanel, panel, tree]);

  const historyReady = Boolean(activeWorkspace && tree?.currentVersionHash);
  const panelOpen = Boolean(
    (panel === "inspector" && selectedNode) ||
      (panel === "agent" && activeWorkspace) ||
      (panel === "history" && historyReady),
  );
  const panelPresence = useExitAnimation(panelOpen, PANEL_EXIT_MS);
  const sidebarPresence = useExitAnimation(!sidebarCollapsed, PANEL_EXIT_MS);

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

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {sidebarPresence.present ? (
        <>
          <Sidebar
            closing={sidebarPresence.closing}
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            onSelectWorkspace={onSelectWorkspace}
            onOpenOptions={(workspace, trigger) =>
              setOptionsMenu({ workspace, anchor: anchorFromEvent(trigger, "left") })
            }
            onNewWorkspace={() => onOpenCreator()}
            onToggleSidebar={onToggleSidebar}
            onOpenAgent={() => (panel === "agent" ? onClosePanel() : onOpenPanel("agent"))}
            agentActive={panel === "agent"}
            agentDisabled={!activeWorkspace || activeRunning}
            buildingRun={buildingRun}
            onResumeBuild={() => onOpenCreator()}
            live={live}
          />
          <div
            className={cx(
              "fixed inset-0 z-backdrop hidden bg-[rgb(31_35_40_/_28%)] max-[900px]:block",
              sidebarPresence.closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
            )}
            role="presentation"
            onClick={onToggleSidebar}
          />
        </>
      ) : null}

      <main className="flex min-w-0 flex-1 flex-col" aria-label="Research workspace">
        <TopBar
          workspaceTitle={tree?.title ?? activeSummary?.title ?? "Research Tree"}
          branchCount={tree?.branchCount ?? null}
          paperCount={tree?.paperCount ?? null}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={onToggleSidebar}
          onOpenSearch={() => setSearchOpen(true)}
          searchDisabled={!tree}
          onToggleHistory={() => (panel === "history" ? onClosePanel() : onOpenPanel("history"))}
          historyActive={panel === "history"}
          historyDisabled={!historyReady}
          onToggleProfile={(trigger) =>
            setProfileAnchor((current) => (current ? null : anchorFromEvent(trigger, "right")))
          }
          profileOpen={profileAnchor !== null}
          user={sessionInfo?.user ?? LOCAL_USER}
        />

        <div className="relative flex min-h-0 flex-1">
          <section
            className="relative min-h-0 min-w-0 flex-1 overflow-hidden"
            aria-live={status === "loading" ? "polite" : "off"}
          >
            {status === "loading" || (status === "ready" && !tree && workspaceLoading) ? (
              <WorkspaceNotice title="Loading workspace" />
            ) : null}
            {status === "error" ? (
              <WorkspaceNotice
                title="Workspaces unavailable"
                detail={error ?? "Your workspaces could not be loaded. Refresh and try again."}
                tone="error"
                actionLabel="Retry"
                onAction={onRefresh}
              />
            ) : null}
            {status === "ready" && !tree && !workspaceLoading && workspaceError ? (
              <WorkspaceNotice
                title="Workspace failed to load"
                detail={workspaceError}
                tone="error"
                actionLabel="Retry"
                onAction={onRefresh}
              />
            ) : null}
            {status === "ready" && !tree && !workspaceLoading && !workspaceError ? (
              <WorkspaceEmptyState
                onCreate={(topic) => onOpenCreator(topic)}
                disabled={isRunActive(buildingRun)}
              />
            ) : null}
            {status === "ready" && tree ? (
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

            {refreshError || signOutError || editor.notice ? (
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
                {editor.notice ? (
                  <Toast
                    key={editor.notice.text}
                    tone={editor.notice.tone}
                    onDismiss={editor.dismissNotice}
                    dismissLabel="Dismiss"
                  >
                    <span className="flex items-center justify-between gap-3">
                      <span className="min-w-0 [overflow-wrap:anywhere]">{editor.notice.text}</span>
                      {editor.notice.undo ? (
                        <button
                          className="flex-none rounded-[5px] border border-accent-border bg-surface px-2 py-0.5 text-[11px] font-semibold text-accent-deep transition-[background-color] duration-150 enabled:hover:bg-accent-subtle disabled:cursor-not-allowed disabled:text-text-muted"
                          type="button"
                          onClick={editor.notice.undo}
                          disabled={editor.busy}
                        >
                          Undo
                        </button>
                      ) : null}
                    </span>
                  </Toast>
                ) : null}
              </ToastStack>
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
              tone={shownPanel === "agent" ? "agent" : "surface"}
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
                  updatedAt={activeSummary?.updated_at ?? null}
                  onSelectNode={onSelectNode}
                  onClose={onClosePanel}
                  onOpenAssistant={() => onOpenPanel("agent")}
                  onOpenActions={(trigger) =>
                    setNodeActions({
                      nodeId: selectedNode.id,
                      anchor: anchorFromEvent(trigger, "right"),
                    })
                  }
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
                <WorkspaceAgent
                  session={session}
                  tree={tree}
                  onClose={onClosePanel}
                />
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
      </main>

      {searchOpen && tree ? (
        <SearchOverlay
          tree={tree}
          onSelectNode={onSelectNode}
          onClose={() => setSearchOpen(false)}
        />
      ) : null}

      {profileAnchor && sessionInfo ? (
        <ProfileMenu
          anchor={profileAnchor}
          session={sessionInfo}
          onClose={() => setProfileAnchor(null)}
          onOpenScreen={setAccountScreen}
          onSignOut={() => {
            setProfileAnchor(null);
            setSignOutError(null);
            signOut().catch((error: unknown) => {
              setSignOutError(
                `Sign-out did not complete, so you are still signed in. ${messageFrom(error)}`,
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
              icon={<ClockIcon className="h-[13px] w-[13px]" />}
              onClick={() => {
                onSelectWorkspace(optionsMenu.workspace.workspace_id);
                onOpenPanel("history");
              }}
            >
              Version history
            </MenuItem>
          </MenuSection>
          <MenuSection>
            <MenuItem
              tone="danger"
              icon={<TrashIcon className="h-[13px] w-[13px]" />}
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

      <WorkspaceCreator
        open={creatorOpen}
        initialTopic={creatorTopic}
        onClose={onCloseCreator}
        onCreated={onCreated}
        onOpenExisting={onSelectWorkspace}
        activeRun={buildingRun}
        onRunStarted={onPipelineStarted}
        onRunFinished={onPipelineFinished}
      />
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
