import type { PipelineRun, WorkspaceSummary } from "../../lib/types";
import { cx } from "../../lib/cx";

type WorkspaceListProps = {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
  collapsed: boolean;
};

export function WorkspaceList({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  buildingRun,
  onResumeBuild,
  collapsed,
}: WorkspaceListProps) {
  const activeBuildingRun = buildingRun && isActiveRun(buildingRun) ? buildingRun : null;
  const buildRunHasWorkspace = workspaces.some(
    (workspace) => workspace.workspace_id === buildingRun?.workspace_id,
  );
  const placeholderRun =
    activeBuildingRun ?? (buildingRun?.status === "failed" && !buildRunHasWorkspace ? buildingRun : null);
  return (
    <nav
      className={cx(
        "scrollbar-rt flex min-h-0 flex-1 flex-col gap-[3px] overflow-y-auto",
        collapsed && "opacity-0 max-[720px]:flex-row max-[720px]:gap-1 max-[720px]:overflow-x-auto max-[720px]:opacity-100",
      )}
      aria-label="Workspaces"
    >
      {workspaces.length === 0 && placeholderRun ? <WorkspaceBuildItem run={placeholderRun} onResume={onResumeBuild} /> : null}
      {workspaces.map((workspace, index) => (
        <div key={workspace.workspace_id}>
          <button
            className="flex w-full flex-col gap-1 rounded-sm border border-[color-mix(in_srgb,var(--color-text-muted)_28%,transparent)] bg-[color-mix(in_srgb,var(--color-surface)_30%,transparent)] p-2.5 text-left transition-[background-color,border-color] duration-200 ease-research hover:bg-[color-mix(in_srgb,var(--color-surface)_74%,transparent)] data-[building=true]:border-[color-mix(in_srgb,var(--color-accent)_55%,var(--color-border))] data-[building=true]:bg-accent-subtle data-[selected=true]:border-[color-mix(in_srgb,var(--color-accent)_55%,var(--color-border))] data-[selected=true]:bg-accent-subtle max-[980px]:min-h-10 max-[980px]:items-center max-[980px]:justify-center max-[980px]:px-[5px] max-[980px]:py-[7px]"
            type="button"
            data-selected={workspace.workspace_id === activeWorkspaceId}
            data-building={activeBuildingRun?.workspace_id === workspace.workspace_id}
            onClick={() => onSelectWorkspace(workspace.workspace_id)}
          >
            <span className="[overflow-wrap:anywhere] text-[13px] font-semibold leading-[1.3] text-text-primary max-[980px]:max-w-[52px] max-[980px]:truncate max-[980px]:text-center max-[980px]:text-[10px]">{workspace.title}</span>
            <small className="text-[11px] leading-[1.35] text-text-secondary max-[980px]:hidden">
              {activeBuildingRun?.workspace_id === workspace.workspace_id ? (
                <>Building<LoadingEllipsis /></>
              ) : (
                <>{workspace.branch_count} research branch{workspace.branch_count === 1 ? "" : "es"}</>
              )}
            </small>
          </button>
          {index === 0 && placeholderRun && !buildRunHasWorkspace ? (
            <WorkspaceBuildItem run={placeholderRun} onResume={onResumeBuild} />
          ) : null}
        </div>
      ))}
    </nav>
  );
}

function WorkspaceBuildItem({ run, onResume }: { run: PipelineRun; onResume: () => void }) {
  return (
    <button className="mt-0.5 grid w-full gap-1 rounded-sm border border-[color-mix(in_srgb,var(--color-text-muted)_48%,transparent)] bg-[color-mix(in_srgb,var(--color-surface)_42%,transparent)] p-2.5 text-left max-[980px]:justify-items-center max-[980px]:p-1.5" type="button" onClick={onResume}>
      <span className="[overflow-wrap:anywhere] text-[13px] font-semibold leading-[1.3] text-text-primary max-[980px]:hidden">{run.topic}</span>
      <span className="hidden h-2 w-2 animate-progress-spin rounded-full border-2 border-accent border-t-transparent max-[980px]:block" aria-hidden="true" />
      <small className={cx("text-[11px] leading-[1.35] text-text-secondary max-[980px]:hidden", run.status === "failed" && "text-error")}>
        {run.status === "failed" ? "Workspace build failed" : <>Building workspace<LoadingEllipsis /></>}
      </small>
    </button>
  );
}

function LoadingEllipsis() {
  return (
    <span className="inline-flex w-[13px] justify-start" aria-hidden="true">
      <i className="animate-loading-dot not-italic">.</i>
      <i className="animate-loading-dot not-italic [animation-delay:120ms]">.</i>
      <i className="animate-loading-dot not-italic [animation-delay:240ms]">.</i>
    </span>
  );
}

function isActiveRun(run: PipelineRun): boolean {
  return run.status === "queued" || run.status === "running";
}
