import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { TreeCanvas } from "../tree/TreeCanvas";
import { FloatingInspector } from "../tree/FloatingInspector";
import { WorkspaceEmptyState } from "../workspace/WorkspaceEmptyState";
import { WorkspaceCreator } from "../workspace/WorkspaceCreator";
import { WorkspaceHistory } from "../workspace/WorkspaceHistory";
import { WorkspaceAgent } from "../workspace/WorkspaceAgent";
import agentIcon from "../../assets/lucide-message-square-text.svg";
import type { PipelineRun, TreeNodeId, TreeViewModel, WorkspaceDocument } from "../../lib/types";

type AppShellProps = {
  status: "loading" | "ready" | "error";
  error: string | null;
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  tree: TreeViewModel | null;
  activeWorkspace: WorkspaceDocument | null;
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
  onResumeBuild: () => void;
  onPipelineStarted: (run: PipelineRun) => void;
  onPipelineFinished: (runId: string) => void;
};

export function AppShell({
  status,
  error,
  workspaces,
  activeWorkspaceId,
  tree,
  activeWorkspace,
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
  onResumeBuild,
  onPipelineStarted,
  onPipelineFinished,
}: AppShellProps) {
  const selectedNode = tree && selectedNodeId ? tree.nodesById[selectedNodeId] ?? null : null;
  const activePipelineRunning =
    buildingRun?.workspace_id === activeWorkspaceId &&
    (buildingRun.status === "queued" || buildingRun.status === "running");

  return (
    <div className="app-shell" data-sidebar-collapsed={sidebarCollapsed}>
      <Sidebar
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
        onNewWorkspace={onOpenCreator}
        collapsed={sidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
        buildingRun={buildingRun}
        onResumeBuild={onResumeBuild}
      />
      <button
        className="persistent-assistant-launcher agent-launcher"
        type="button"
        onClick={() => onOpenUtility("agent")}
        disabled={!tree || activePipelineRunning}
        aria-label="Open Assistant"
        title="Assistant"
      >
        <img src={agentIcon} alt="" aria-hidden="true" />
      </button>

      <main className="workspace-main" aria-label="Research workspace">
        <TopBar
          workspaceTitle={tree?.title ?? "Workspace"}
          tree={tree}
          onSelectNode={onSelectNode}
          onOpenHistory={() => onOpenUtility("history")}
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={onToggleSidebar}
        />
        <section className="workspace-stage" aria-live={status === "loading" ? "polite" : "off"}>
          {status === "loading" ? (
            <WorkspaceEmptyState title="Loading workspace" detail="Loading…" />
          ) : null}
          {status === "error" ? (
            <WorkspaceEmptyState
              title="Workspace unavailable"
              detail={error ?? "Your workspaces could not be loaded. Refresh and try again."}
              tone="error"
            />
          ) : null}
          {status === "ready" && !tree ? (
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
              {activeWorkspace && tree?.currentVersionHash ? (
                <WorkspaceHistory
                  open={utilityPanel === "history"}
                  workspaceId={activeWorkspace.workspace_id}
                  workspaceTitle={activeWorkspace.title}
                  currentVersionHash={tree.currentVersionHash}
                  versions={activeWorkspace.workspace_versions ?? []}
                  onClose={onCloseUtility}
                  onChanged={onWorkspaceChanged}
                  onDeleted={onWorkspaceDeleted}
                />
              ) : null}
              {activeWorkspace ? (
                <WorkspaceAgent
                  open={utilityPanel === "agent"}
                  workspaceId={activeWorkspace.workspace_id}
                  sidebarCollapsed={sidebarCollapsed}
                  onClose={onCloseUtility}
                  onWorkspaceChanged={onWorkspaceChanged}
                />
              ) : null}
            </>
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
