import { useEffect, useRef, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ProfileMenu, type AccountScreen } from "./ProfileMenu";
import { AccountScreens, useApiKeyLabel } from "../account/AccountScreens";
import { TreeCanvas } from "../tree/TreeCanvas";
import { SearchOverlay } from "../search/SearchOverlay";
import { NodeInspector, inspectorLabel } from "../inspector/NodeInspector";
import { RightPanel, RIGHT_PANEL_DEFAULT_WIDTH } from "../panel/RightPanel";
import { WorkspaceEmptyState, WorkspaceNotice } from "../workspace/WorkspaceEmptyState";
import { WorkspaceCreator } from "../workspace/WorkspaceCreator";
import { WorkspaceHistory } from "../workspace/WorkspaceHistory";
import { WorkspaceAgent } from "../workspace/WorkspaceAgent";
import { DeleteWorkspaceDialog } from "../workspace/DeleteWorkspaceDialog";
import { Toast, ToastStack } from "../ui/Toast";
import { MenuItem, MenuSection, PopoverMenu, anchorFromEvent, type MenuAnchor } from "../ui/PopoverMenu";
import { ClockIcon, TrashIcon } from "../ui/icons";
import { cx } from "../../lib/cx";
import { useAgentSession } from "../../data/useAgentSession";
import { PANEL_EXIT_MS, useExitAnimation } from "../../lib/animation";
import { isRunActive } from "../../lib/pipelineStages";
import type {
  PipelineRun,
  TreeNodeId,
  TreeViewModel,
  WorkspaceDocument,
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
  onWorkspaceDeleted: () => Promise<void>;
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
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceSummary | null>(null);
  const [accountScreen, setAccountScreen] = useState<AccountScreen | null>(null);
  const [panelWidth, setPanelWidth] = useState(RIGHT_PANEL_DEFAULT_WIDTH);
  // A failed background refresh keeps the workspaces already on screen, so the
  // failure has nowhere else to appear. Dismissal is tracked by message, so a
  // later — different — failure still speaks up.
  const [dismissedError, setDismissedError] = useState<string | null>(null);

  const activeWorkspaceId = activeSummary?.workspace_id ?? null;
  const selectedNode = tree && selectedNodeId ? (tree.nodesById[selectedNodeId] ?? null) : null;
  const activeRunning =
    buildingRun?.workspace_id === activeWorkspaceId && isRunActive(buildingRun);
  const refreshError = status !== "error" && error && error !== dismissedError ? error : null;
  const session = useAgentSession(activeWorkspace?.workspace_id ?? null, onWorkspaceChanged);
  const apiKeyLabel = useApiKeyLabel();

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
        />

        <div className="relative flex min-h-0 flex-1">
          <section
            className="relative min-h-0 min-w-0 flex-1 overflow-hidden"
            aria-live={status === "loading" ? "polite" : "off"}
          >
            {status === "loading" || (status === "ready" && !tree && workspaceLoading) ? (
              <WorkspaceNotice title="Loading workspace" detail="Reading the saved structure…" />
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
              <TreeCanvas
                tree={tree}
                workspace={activeWorkspace}
                selectedNodeId={selectedNodeId}
                onSelectNode={onSelectNode}
              />
            ) : null}

            {refreshError ? (
              <ToastStack>
                <Toast
                  tone="error"
                  onDismiss={() => setDismissedError(refreshError)}
                  dismissLabel="Dismiss workspace refresh error"
                >
                  {refreshError}
                </Toast>
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
              width={panelWidth}
              onWidth={setPanelWidth}
              sidebarCollapsed={sidebarCollapsed}
            >
              {shownPanel === "inspector" && selectedNode && tree ? (
                <NodeInspector
                  node={selectedNode}
                  tree={tree}
                  updatedAt={activeSummary?.updated_at ?? null}
                  onSelectNode={onSelectNode}
                  onClose={onClosePanel}
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

      {profileAnchor ? (
        <ProfileMenu
          anchor={profileAnchor}
          onClose={() => setProfileAnchor(null)}
          onOpenScreen={setAccountScreen}
          apiKeyLabel={apiKeyLabel}
        />
      ) : null}

      <AccountScreens
        screen={accountScreen}
        onClose={() => setAccountScreen(null)}
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
              onClick={() => setDeleteTarget(optionsMenu.workspace)}
            >
              Delete workspace…
            </MenuItem>
          </MenuSection>
        </PopoverMenu>
      ) : null}

      {deleteTarget ? (
        <DeleteWorkspaceDialog
          workspace={deleteTarget}
          onCancel={() => setDeleteTarget(null)}
          onChanged={onWorkspaceChanged}
          onDeleted={async () => {
            setDeleteTarget(null);
            await onWorkspaceDeleted();
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
