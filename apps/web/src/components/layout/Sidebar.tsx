import { WorkspaceList } from "../workspace/WorkspaceList";
import type { PipelineRun, WorkspaceDocument } from "../../lib/types";

type SidebarProps = {
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onNewWorkspace: () => void;
  collapsed: boolean;
  onToggleSidebar: () => void;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
};

export function Sidebar({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onNewWorkspace,
  collapsed,
  onToggleSidebar,
  buildingRun,
  onResumeBuild,
}: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Workspace navigation">
      <div className="sidebar-header">
        <div>
          <h1>Research Tree</h1>
          <p>{workspaces.length} workspaces</p>
        </div>
        <button
          className="sidebar-collapse-button icon-button"
          type="button"
          onClick={onToggleSidebar}
          aria-label={collapsed ? "Show workspace sidebar" : "Hide workspace sidebar"}
          title={collapsed ? "Show sidebar" : "Hide sidebar"}
        >
          <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3.5" width="14" height="13" rx="2" />
            <path d="M8.5 3.5v13" />
          </svg>
        </button>
      </div>

      <button className="new-workspace-button" type="button" onClick={onNewWorkspace}>
        + New workspace
      </button>

      <WorkspaceList
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
        buildingRun={buildingRun}
        onResumeBuild={onResumeBuild}
      />

      <div className="sidebar-footer" />
    </aside>
  );
}
