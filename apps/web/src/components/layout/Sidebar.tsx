import { WorkspaceList } from "../workspace/WorkspaceList";
import type { WorkspaceDocument } from "../../lib/types";

type SidebarProps = {
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
};

export function Sidebar({ workspaces, activeWorkspaceId, onSelectWorkspace }: SidebarProps) {
  return (
    <aside className="sidebar" aria-label="Workspace navigation">
      <div className="sidebar-header">
        <div>
          <h1>Research Tree</h1>
          <p>{workspaces.length} workspaces</p>
        </div>
      </div>

      <button className="new-workspace-button" type="button" disabled>
        + New workspace
      </button>

      <WorkspaceList
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
      />

      <div className="profile-panel">
        <div className="profile-mark" aria-hidden="true">
          RT
        </div>
        <div className="profile-copy">
          <span>Local profile</span>
          <button type="button" disabled>
            Sign in later
          </button>
        </div>
      </div>
    </aside>
  );
}
