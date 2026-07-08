import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { TreeCanvas } from "../tree/TreeCanvas";
import { FloatingInspector } from "../tree/FloatingInspector";
import { WorkspaceEmptyState } from "../workspace/WorkspaceEmptyState";
import type { TreeNodeId, TreeViewModel, WorkspaceDocument } from "../../lib/types";

type AppShellProps = {
  status: "loading" | "ready" | "error";
  error: string | null;
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  tree: TreeViewModel | null;
  selectedNodeId: TreeNodeId;
  onSelectWorkspace: (workspaceId: string) => void;
  onSelectNode: (nodeId: TreeNodeId) => void;
};

export function AppShell({
  status,
  error,
  workspaces,
  activeWorkspaceId,
  tree,
  selectedNodeId,
  onSelectWorkspace,
  onSelectNode,
}: AppShellProps) {
  const selectedNode = tree?.nodesById[selectedNodeId] ?? null;

  return (
    <div className="app-shell">
      <Sidebar
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
      />
      <main className="workspace-main" aria-label="Research workspace">
        <TopBar workspaceTitle={tree?.title ?? "Workspace"} />
        <section className="workspace-stage" aria-live={status === "loading" ? "polite" : "off"}>
          {status === "loading" ? (
            <WorkspaceEmptyState title="Loading workspace" detail="Preparing the local workspace tree." />
          ) : null}
          {status === "error" ? (
            <WorkspaceEmptyState
              title="Workspace unavailable"
              detail={error ?? "The workspace fixture could not be loaded."}
              tone="error"
            />
          ) : null}
          {status === "ready" && !tree ? (
            <WorkspaceEmptyState
              title="No workspaces"
              detail="Create or import a topic workspace to begin mapping papers."
            />
          ) : null}
          {status === "ready" && tree ? (
            <>
              <TreeCanvas
                tree={tree}
                selectedNodeId={selectedNodeId}
                onSelectNode={onSelectNode}
              />
              <FloatingInspector node={selectedNode} />
            </>
          ) : null}
        </section>
      </main>
    </div>
  );
}
