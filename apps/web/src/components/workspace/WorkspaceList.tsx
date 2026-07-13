import type { PipelineRun, WorkspaceDocument } from "../../lib/types";

type WorkspaceListProps = {
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
};

export function WorkspaceList({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  buildingRun,
  onResumeBuild,
}: WorkspaceListProps) {
  const activeBuildingRun = buildingRun && isActiveRun(buildingRun) ? buildingRun : null;
  const buildRunHasWorkspace = workspaces.some(
    (workspace) => workspace.workspace_id === buildingRun?.workspace_id,
  );
  const placeholderRun =
    activeBuildingRun ?? (buildingRun?.status === "failed" && !buildRunHasWorkspace ? buildingRun : null);
  return (
    <nav className="workspace-list" aria-label="Workspaces">
      {workspaces.length === 0 && placeholderRun ? <WorkspaceBuildItem run={placeholderRun} onResume={onResumeBuild} /> : null}
      {workspaces.map((workspace, index) => (
        <div key={workspace.workspace_id} className="workspace-list-entry">
          <button
            type="button"
            data-selected={workspace.workspace_id === activeWorkspaceId}
            data-building={activeBuildingRun?.workspace_id === workspace.workspace_id}
            onClick={() => onSelectWorkspace(workspace.workspace_id)}
          >
            <span>{workspace.title}</span>
            <small>
              {activeBuildingRun?.workspace_id === workspace.workspace_id ? (
                <>Building<span className="loading-ellipsis" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span></>
              ) : (
                <>{workspace.tree.nodes.length} research branch{workspace.tree.nodes.length === 1 ? "" : "es"}</>
              )}
            </small>
          </button>
          {index === 0 && placeholderRun && placeholderRun.workspace_id !== workspace.workspace_id ? (
            <WorkspaceBuildItem run={placeholderRun} onResume={onResumeBuild} />
          ) : null}
        </div>
      ))}
    </nav>
  );
}

function WorkspaceBuildItem({ run, onResume }: { run: PipelineRun; onResume: () => void }) {
  return (
    <button className="workspace-build-item" type="button" onClick={onResume}>
      <span>{run.topic}</span>
      <small data-status={run.status}>
        {run.status === "failed" ? "Workspace build failed" : <>Building workspace<span className="loading-ellipsis" aria-hidden="true"><i>.</i><i>.</i><i>.</i></span></>}
      </small>
    </button>
  );
}

function isActiveRun(run: PipelineRun): boolean {
  return run.status === "queued" || run.status === "running";
}
