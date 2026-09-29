import { cx } from "../../lib/cx";
import { relativeTimestamp, pluralize } from "../../lib/format";
import { isRunActive, useBuildProgress } from "../../lib/pipelineStages";
import { EllipsisIcon } from "../ui/icons";
import type { PipelineRun, WorkspaceSummary } from "../../lib/types";

type WorkspaceListProps = {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onOpenOptions: (workspace: WorkspaceSummary, trigger: HTMLElement) => void;
  /** The workspace whose options menu is open, which keeps its row marked. */
  optionsOpenFor: string | null;
  buildingRun: PipelineRun | null;
  /** Shows the build's progress on the new-workspace page. */
  onOpenBuild: () => void;
};

export function WorkspaceList({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onOpenOptions,
  optionsOpenFor,
  buildingRun,
  onOpenBuild,
}: WorkspaceListProps) {
  const activeRun = isRunActive(buildingRun) ? buildingRun : null;
  const runHasWorkspace = workspaces.some(
    (workspace) => workspace.workspace_id === buildingRun?.workspace_id,
  );
  // A build whose workspace has not landed yet, or never will, still needs a
  // row: it is the way back to its progress or its failure.
  const placeholderRun = buildingRun && !runHasWorkspace ? buildingRun : null;

  return (
    <nav
      className="scrollbar-rt flex min-h-0 flex-1 flex-col gap-px overflow-y-auto px-2.5 pb-3"
      aria-label="Workspaces"
    >
      {placeholderRun ? <BuildPlaceholderRow run={placeholderRun} onOpen={onOpenBuild} /> : null}
      {workspaces.map((workspace) => {
        const selected = workspace.workspace_id === activeWorkspaceId;
        const optionsOpen = workspace.workspace_id === optionsOpenFor;
        const building = activeRun?.workspace_id === workspace.workspace_id ? activeRun : null;
        return (
          <div
            key={workspace.workspace_id}
            className={cx(
              "group relative flex items-center gap-1 rounded-md transition-[background-color] duration-150",
              selected ? "bg-accent-subtle" : optionsOpen ? "bg-sidebar-hover" : "hover:bg-sidebar-hover",
            )}
          >
            <button
              className="min-w-0 flex-1 border-0 bg-transparent py-[7px] pr-1 pl-2.5 text-left"
              type="button"
              aria-current={selected ? "page" : undefined}
              onClick={() => onSelectWorkspace(workspace.workspace_id)}
            >
              <span
                className={cx(
                  "block truncate text-[13px] leading-[1.4]",
                  selected ? "font-semibold text-accent-deep" : "font-medium text-text-primary",
                )}
              >
                {workspace.title}
              </span>
              {building ? (
                <BuildingCaption run={building} />
              ) : (
                <span className="block truncate text-[11.5px] leading-[1.4] text-text-muted">
                  {workspaceCaption(workspace)}
                </span>
              )}
            </button>
            <button
              className={cx(
                "mr-1.5 grid h-6 w-6 flex-none place-items-center rounded-[6px] border-0 bg-transparent p-0 opacity-0 transition-[background-color,color,opacity] duration-150 group-hover:opacity-100 focus-visible:opacity-100 aria-expanded:opacity-100",
                selected
                  ? "text-accent-deep hover:bg-accent-border"
                  : "text-text-muted hover:bg-hairline hover:text-text-primary",
              )}
              type="button"
              onClick={(event) => onOpenOptions(workspace, event.currentTarget)}
              aria-haspopup="menu"
              aria-expanded={optionsOpen}
              aria-label={`Options for ${workspace.title}`}
              title="Workspace options"
            >
              <EllipsisIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </nav>
  );
}

function BuildingCaption({ run }: { run: PipelineRun }) {
  const progress = useBuildProgress(run);
  return (
    <>
      <span className="mb-1.5 block truncate text-[11.5px] leading-[1.4] text-text-muted">
        {buildingLabel(run, progress.currentLabel)}
      </span>
      <ProgressBar percent={progress.percent} />
    </>
  );
}

function BuildPlaceholderRow({ run, onOpen }: { run: PipelineRun; onOpen: () => void }) {
  const progress = useBuildProgress(run);
  const stopped = !isRunActive(run);
  return (
    <button
      className="rounded-md border-0 bg-transparent px-2.5 py-[7px] text-left transition-[background-color] duration-150 hover:bg-sidebar-hover"
      type="button"
      onClick={onOpen}
    >
      <span className="block truncate text-[13px] font-medium leading-[1.4] text-text-primary">
        {run.topic}
      </span>
      {stopped ? (
        <span
          className={cx(
            "block truncate text-[11.5px] leading-[1.4]",
            run.status === "failed" ? "text-error" : "text-text-muted",
          )}
        >
          {run.status === "failed" ? "Build failed" : "Build cancelled"}
        </span>
      ) : (
        <>
          <span className="mb-1.5 block truncate text-[11.5px] leading-[1.4] text-text-muted">
            {buildingLabel(run, progress.currentLabel)}
          </span>
          <ProgressBar percent={progress.percent} />
        </>
      )}
    </button>
  );
}

/** A build in the queue has not started, and says so rather than "Building". */
function buildingLabel(run: PipelineRun, currentLabel: string | null): string {
  if (run.status === "queued") return "Waiting for a free worker…";
  return `Building${currentLabel ? ` · ${lowerFirst(currentLabel)}` : ""}…`;
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <span className="mb-0.5 block h-[3px] overflow-hidden rounded-full bg-hairline" aria-hidden="true">
      <span
        className="block h-full rounded-full bg-accent transition-[width] duration-500 ease-linear"
        style={{ width: `${percent}%` }}
      />
    </span>
  );
}

function workspaceCaption(workspace: WorkspaceSummary): string {
  const updated = relativeTimestamp(workspace.updated_at);
  const papers = pluralize(workspace.paper_count, "paper");
  return updated ? `${papers} · ${updated}` : papers;
}

function lowerFirst(value: string): string {
  return value.charAt(0).toLowerCase() + value.slice(1);
}
