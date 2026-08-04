import { WorkspaceList } from "../workspace/WorkspaceList";
import { cx } from "../../lib/cx";
import { pluralize } from "../../lib/format";
import { isRunActive } from "../../lib/pipelineStages";
import { ChatIcon, PlusIcon, SidebarIcon } from "../ui/icons";
import type { PipelineRun, WorkspaceSummary } from "../../lib/types";

type SidebarProps = {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onOpenOptions: (workspace: WorkspaceSummary, trigger: HTMLElement) => void;
  onNewWorkspace: () => void;
  onToggleSidebar: () => void;
  onOpenAgent: () => void;
  agentActive: boolean;
  agentDisabled: boolean;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
  /** True while the sidebar plays its exit animation on the way out. */
  closing?: boolean;
  /** False once the workspace event stream drops; the list stops self-updating. */
  live: boolean;
};

export function Sidebar({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onOpenOptions,
  onNewWorkspace,
  onToggleSidebar,
  onOpenAgent,
  agentActive,
  agentDisabled,
  buildingRun,
  onResumeBuild,
  closing = false,
  live,
}: SidebarProps) {
  const building = isRunActive(buildingRun);

  return (
    <aside
      className={cx(
        "flex w-[250px] flex-none flex-col border-r border-border bg-sidebar min-h-0 max-[900px]:fixed max-[900px]:inset-y-0 max-[900px]:left-0 max-[900px]:z-overlay max-[900px]:shadow-popover",
        closing ? "animate-interface-left-exit" : "animate-interface-left-enter",
      )}
      aria-label="Workspace navigation"
    >
      <div className="flex items-center gap-[9px] pt-3.5 pr-3 pb-3 pl-4">
        <h1 className="m-0 flex-1 text-sm font-bold tracking-[-0.01em] text-text-primary">
          Research Tree
        </h1>
        <button
          className="grid h-[26px] w-[26px] flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-[#e4e7e9] hover:text-text-primary"
          type="button"
          onClick={onToggleSidebar}
          aria-label="Hide workspace sidebar"
          title="Collapse sidebar"
        >
          <SidebarIcon className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="px-3 pt-0.5 pb-2.5">
        <button
          className="flex w-full items-center justify-center gap-[7px] rounded-md border border-border bg-surface px-3 py-[7px] text-[12.5px] font-semibold text-text-primary transition-[background-color,border-color,color] duration-150 enabled:hover:border-border-strong enabled:hover:bg-surface-muted disabled:cursor-not-allowed disabled:border-border disabled:bg-surface-subtle disabled:text-text-muted"
          type="button"
          onClick={onNewWorkspace}
          disabled={building}
          title={building ? "A workspace is already building" : undefined}
        >
          {building ? (
            "Building workspace…"
          ) : (
            <>
              <PlusIcon className="h-[11px] w-[11px]" />
              New workspace
            </>
          )}
        </button>
      </div>

      <div className="px-4 pt-2 pb-1.5 text-[10.5px] font-semibold tracking-[0.06em] text-text-muted uppercase">
        Workspaces
      </div>

      <WorkspaceList
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
        onOpenOptions={onOpenOptions}
        buildingRun={buildingRun}
        onResumeBuild={onResumeBuild}
      />

      <div className="border-t border-border p-2">
        <button
          className={cx(
            "flex w-full items-center gap-[9px] rounded-md border-0 px-2.5 py-2 text-left text-[12.5px] font-semibold text-text-primary transition-[background-color,color] duration-150 disabled:cursor-not-allowed disabled:text-text-muted",
            agentActive ? "bg-accent-subtle" : "bg-transparent enabled:hover:bg-[#e4e7e9]",
          )}
          type="button"
          onClick={onOpenAgent}
          disabled={agentDisabled}
          title={agentDisabled ? "Open a workspace to use the assistant" : "Assistant"}
        >
          <ChatIcon
            className={cx("h-[15px] w-[15px]", agentDisabled ? "text-border-strong" : "text-accent-deep")}
          />
          Assistant
        </button>
        <div className="flex items-center gap-2 px-2.5 pt-[7px] pb-0.5">
          <span className="flex-1 text-[11px] text-text-muted">
            {pluralize(workspaces.length, "workspace")}
          </span>
          {!live ? (
            <span
              className="flex items-center gap-1.5 text-[11px] text-text-muted"
              role="status"
              title="The workspace event stream dropped. Reconnecting…"
            >
              <span className="h-1.5 w-1.5 flex-none rounded-full bg-text-muted" aria-hidden="true" />
              Live paused
            </span>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
