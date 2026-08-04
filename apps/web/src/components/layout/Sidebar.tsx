import { WorkspaceList } from "../workspace/WorkspaceList";
import { cx } from "../../lib/cx";
import type { PipelineRun, WorkspaceSummary } from "../../lib/types";

type SidebarProps = {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onNewWorkspace: () => void;
  collapsed: boolean;
  onToggleSidebar: () => void;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
  /** False once the workspace event stream drops; the list stops self-updating. */
  live: boolean;
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
  live,
}: SidebarProps) {
  const buildingActive =
    buildingRun?.status === "queued" || buildingRun?.status === "running";
  return (
    <aside
      className={cx(
        "flex h-screen w-[252px] min-w-[252px] flex-col gap-[18px] overflow-hidden border-r border-border bg-surface-subtle px-3.5 py-5 pb-4 opacity-100 transition-[border-color,opacity,transform] duration-200 ease-research max-[980px]:w-[68px] max-[980px]:min-w-[68px] max-[980px]:gap-4 max-[980px]:px-2 max-[980px]:py-4 max-[980px]:pb-3 max-[420px]:w-[60px] max-[420px]:min-w-[60px]",
        collapsed &&
          "pointer-events-none w-[252px] min-w-[252px] -translate-x-[252px] border-0 p-0 opacity-0 max-[720px]:pointer-events-auto max-[720px]:h-auto max-[720px]:w-auto max-[720px]:min-w-0 max-[720px]:translate-x-0 max-[720px]:flex-row max-[720px]:items-center max-[720px]:gap-4 max-[720px]:overflow-visible max-[720px]:border-r-0 max-[720px]:border-b max-[720px]:border-border max-[720px]:px-3 max-[720px]:py-2 max-[720px]:opacity-100",
      )}
      aria-label="Workspace navigation"
    >
      <div
        className={cx(
          "flex min-w-0 flex-none items-center justify-between gap-3 max-[980px]:justify-center",
          collapsed && "opacity-0 max-[720px]:opacity-100",
        )}
      >
        <div className="max-[980px]:hidden">
          <h1 className="m-0 whitespace-nowrap text-[19px] font-bold leading-[1.2] tracking-normal text-text-primary">Research Tree</h1>
          <p className="mt-[5px] mb-0 whitespace-nowrap text-[11px] leading-[1.3] text-text-secondary">{workspaces.length} workspace{workspaces.length === 1 ? "" : "s"}</p>
          {!live ? (
            <p className="mt-[3px] mb-0 flex items-center gap-1.5 whitespace-nowrap text-[11px] leading-[1.3] text-text-muted" role="status" title="The workspace event stream dropped. Reconnecting…">
              <span className="h-1.5 w-1.5 flex-none rounded-full bg-text-muted" aria-hidden="true" />
              Live updates paused
            </p>
          ) : null}
        </div>
        <button
          className="grid h-8 w-8 flex-none place-items-center rounded-md border border-transparent bg-transparent p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:border-border enabled:hover:bg-surface enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px]"
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

      <button
        className={cx(
          "min-h-[38px] w-full flex-none rounded-sm border border-border-strong bg-surface px-[11px] py-2 text-left text-xs font-semibold text-text-primary transition-[background-color,border-color,color] duration-200 ease-research enabled:hover:border-accent enabled:hover:bg-accent-subtle enabled:hover:text-accent-deep disabled:cursor-not-allowed disabled:text-text-muted max-[980px]:hidden",
          collapsed && "opacity-0 max-[720px]:hidden",
        )}
        type="button"
        onClick={onNewWorkspace}
        disabled={buildingActive}
        title={buildingActive ? "A workspace is already building" : undefined}
      >
        {buildingActive ? "Building workspace…" : "+ New workspace"}
      </button>

      <WorkspaceList
        workspaces={workspaces}
        activeWorkspaceId={activeWorkspaceId}
        onSelectWorkspace={onSelectWorkspace}
        buildingRun={buildingRun}
        onResumeBuild={onResumeBuild}
        collapsed={collapsed}
      />

    </aside>
  );
}
