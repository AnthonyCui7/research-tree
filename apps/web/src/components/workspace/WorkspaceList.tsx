import type { WorkspaceDocument } from "../../lib/types";

type WorkspaceListProps = {
  workspaces: WorkspaceDocument[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
};

export function WorkspaceList({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
}: WorkspaceListProps) {
  return (
    <nav className="workspace-list" aria-label="Workspaces">
      {workspaces.map((workspace) => (
        <button
          key={workspace.workspace_id}
          type="button"
          data-selected={workspace.workspace_id === activeWorkspaceId}
          onClick={() => onSelectWorkspace(workspace.workspace_id)}
        >
          <span>{workspace.title}</span>
          <small>{workspace.paper_paths.length} paper paths</small>
        </button>
      ))}
    </nav>
  );
}
