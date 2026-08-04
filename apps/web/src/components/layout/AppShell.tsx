import { useEffect, useState } from "react";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { TreeCanvas } from "../tree/TreeCanvas";
import { FloatingInspector } from "../tree/FloatingInspector";
import { WorkspaceEmptyState } from "../workspace/WorkspaceEmptyState";
import { WorkspaceCreator } from "../workspace/WorkspaceCreator";
import { WorkspaceHistory } from "../workspace/WorkspaceHistory";
import { WorkspaceAgent } from "../workspace/WorkspaceAgent";
import agentIcon from "../../assets/lucide-message-square-text.svg";
import { cx } from "../../lib/cx";
import type { PipelineRun, TreeNodeId, TreeViewModel, WorkspaceDocument, WorkspaceSummary } from "../../lib/types";

type AppShellProps = {
  status: "loading" | "ready" | "error";
  error: string | null;
  live: boolean;
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  tree: TreeViewModel | null;
  activeWorkspace: WorkspaceDocument | null;
  workspaceLoading: boolean;
  workspaceError: string | null;
  selectedNodeId: TreeNodeId | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectNode: (nodeId: TreeNodeId) => void;
  onCloseInspector: () => void;
  creatorOpen: boolean;
  utilityPanel: "history" | "agent" | null;
  sidebarCollapsed: boolean;
  buildingRun: PipelineRun | null;
  onOpenCreator: () => void;
  onCloseCreator: () => void;
  onCreated: (workspaceId: string, run: PipelineRun) => Promise<void>;
  onOpenUtility: (panel: "history" | "agent" | null) => void;
  onCloseUtility: () => void;
  onWorkspaceChanged: () => Promise<void>;
  onWorkspaceDeleted: () => Promise<void>;
  onToggleSidebar: () => void;
  onRefresh: () => void;
  onResumeBuild: () => void;
  onPipelineStarted: (run: PipelineRun) => void;
  onPipelineFinished: (runId: string) => void;
};

export function AppShell({
  status,
  error,
  live,
  workspaces,
  activeWorkspaceId,
  tree,
  activeWorkspace,
  workspaceLoading,
  workspaceError,
  selectedNodeId,
  onSelectWorkspace,
  onSelectNode,
  onCloseInspector,
  creatorOpen,
  utilityPanel,
  sidebarCollapsed,
  buildingRun,
  onOpenCreator,
  onCloseCreator,
  onCreated,
  onOpenUtility,
  onCloseUtility,
  onWorkspaceChanged,
  onWorkspaceDeleted,
  onToggleSidebar,
  onRefresh,
  onResumeBuild,
  onPipelineStarted,
  onPipelineFinished,
}: AppShellProps) {
  // A failed background refresh keeps the workspaces already on screen, so the
  // failure has nowhere else to appear. Dismissal is tracked by message, so a
  // later — different — failure still speaks up.
  const [dismissedError, setDismissedError] = useState<string | null>(null);
  const selectedNode = tree && selectedNodeId ? tree.nodesById[selectedNodeId] ?? null : null;
  const activePipelineRunning =
    buildingRun?.workspace_id === activeWorkspaceId &&
    (buildingRun.status === "queued" || buildingRun.status === "running");
  const refreshError =
    status !== "error" && error && error !== dismissedError ? error : null;
  // A refresh that succeeds re-arms the strip, so the same failure returning
  // after a good refresh is reported again rather than silently swallowed.
  useEffect(() => {
    if (!error) {
      setDismissedError(null);
    }
  }, [error]);

  return (
    <div
      className={cx(
        "grid h-screen w-screen bg-surface transition-[grid-template-columns] duration-[260ms] ease-research",
        sidebarCollapsed
          ? "grid-cols-[0_minmax(0,1fr)] max-[720px]:grid-cols-[minmax(0,1fr)] max-[720px]:grid-rows-[56px_minmax(0,1fr)]"
          : "grid-cols-[252px_minmax(0,1fr)] max-[980px]:grid-cols-[68px_minmax(0,1fr)] max-[420px]:grid-cols-[60px_minmax(0,1fr)]",
      )}
    >
      <Sidebar
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
        onNewWorkspace={onOpenCreator}
        collapsed={sidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
        buildingRun={buildingRun}
        onResumeBuild={onResumeBuild}
        live={live}
      />
      <button
        className="fixed bottom-4 left-4 z-[calc(var(--z-panel)_+_1)] grid h-10 w-10 flex-none place-items-center rounded-full border-0 bg-accent p-0 text-surface shadow-launcher transition-none enabled:hover:bg-accent-deep disabled:cursor-not-allowed disabled:bg-border-strong [&_img]:h-5 [&_img]:w-5"
        type="button"
        onClick={() => onOpenUtility("agent")}
        disabled={!tree || activePipelineRunning}
        aria-label="Open Assistant"
        title="Assistant"
      >
        <img src={agentIcon} alt="" aria-hidden="true" />
      </button>

      <main className="grid h-full min-w-0 grid-rows-[64px_minmax(0,1fr)] max-[720px]:grid-rows-[auto_minmax(0,1fr)]" aria-label="Research workspace">
        <TopBar
          workspaceTitle={tree?.title ?? "Workspace"}
          tree={tree}
          onSelectNode={onSelectNode}
          onOpenHistory={() => onOpenUtility("history")}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={onToggleSidebar}
        />
        <section className="relative min-h-0 min-w-0 overflow-hidden bg-background" aria-live={status === "loading" ? "polite" : "off"}>
          {status === "loading" || (status === "ready" && !tree && workspaceLoading) ? (
            <WorkspaceEmptyState title="Loading workspace" detail="Loading…" />
          ) : null}
          {status === "error" ? (
            <WorkspaceEmptyState
              title="Workspaces unavailable"
              detail={error ?? "Your workspaces could not be loaded. Refresh and try again."}
              tone="error"
              actionLabel="Retry"
              onAction={onRefresh}
            />
          ) : null}
          {status === "ready" && !tree && !workspaceLoading && workspaceError ? (
            <WorkspaceEmptyState
              title="Workspace failed to load"
              detail={workspaceError}
              tone="error"
              actionLabel="Retry"
              onAction={onRefresh}
            />
          ) : null}
          {status === "ready" && !tree && !workspaceLoading && !workspaceError ? (
            <WorkspaceEmptyState
              title="No workspaces"
              detail="Enter a topic to build a workspace."
              actionLabel="Create workspace"
              onAction={onOpenCreator}
            />
          ) : null}
          {status === "ready" && tree ? (
            <>
              <TreeCanvas
                tree={tree}
                workspace={activeWorkspace}
                selectedNodeId={selectedNodeId}
                onSelectNode={onSelectNode}
              />
              {utilityPanel === null ? (
                <FloatingInspector
                  node={selectedNode}
                  onClose={onCloseInspector}
                  sidebarCollapsed={sidebarCollapsed}
                />
              ) : null}
              {/* Keying on the workspace id discards panel state on a switch.
                  Without it a pending review, an armed delete confirmation, or a
                  chat transcript would carry over onto a different workspace. */}
              {activeWorkspace && tree?.currentVersionHash ? (
                <WorkspaceHistory
                  key={`history:${activeWorkspace.workspace_id}`}
                  open={utilityPanel === "history"}
                  workspaceId={activeWorkspace.workspace_id}
                  workspaceTitle={activeWorkspace.title}
                  currentVersionHash={tree.currentVersionHash}
                  onClose={onCloseUtility}
                  onChanged={onWorkspaceChanged}
                  onDeleted={onWorkspaceDeleted}
                />
              ) : null}
              {activeWorkspace ? (
                <WorkspaceAgent
                  key={`agent:${activeWorkspace.workspace_id}`}
                  open={utilityPanel === "agent"}
                  workspaceId={activeWorkspace.workspace_id}
                  sidebarCollapsed={sidebarCollapsed}
                  onClose={onCloseUtility}
                  onWorkspaceChanged={onWorkspaceChanged}
                />
              ) : null}
            </>
          ) : null}
          {refreshError ? (
            <div className="absolute right-6 bottom-6 z-[2] flex max-w-[min(420px,calc(100%_-_48px))] items-start gap-2 rounded-sm border border-[color-mix(in_srgb,var(--color-error)_26%,transparent)] bg-[color-mix(in_srgb,var(--color-error)_9%,var(--color-surface))] px-3 py-2.5 text-xs leading-[1.45] text-error shadow-control max-[720px]:right-3 max-[720px]:bottom-3" role="alert">
              <span className="min-w-0">{refreshError}</span>
              <button
                className="grid h-5 w-5 flex-none place-items-center rounded-sm border-0 bg-transparent p-0 text-error transition-[background-color] duration-150 hover:bg-[color-mix(in_srgb,var(--color-error)_14%,transparent)] [&_svg]:h-3.5 [&_svg]:w-3.5 max-[720px]:h-8 max-[720px]:w-8"
                type="button"
                onClick={() => setDismissedError(refreshError)}
                aria-label="Dismiss workspace refresh error"
                title="Dismiss"
              >
                <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
              </button>
            </div>
          ) : null}
        </section>
      </main>
      <WorkspaceCreator
        open={creatorOpen}
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
