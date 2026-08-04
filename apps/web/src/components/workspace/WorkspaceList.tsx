import { cx } from "../../lib/cx";
import { relativeTimestamp, pluralize } from "../../lib/format";
import { buildProgress, isRunActive } from "../../lib/pipelineStages";
import { EllipsisIcon } from "../ui/icons";
import type { PipelineRun, WorkspaceSummary } from "../../lib/types";

type WorkspaceListProps = {
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: string | null;
  onSelectWorkspace: (workspaceId: string) => void;
  onOpenOptions: (workspace: WorkspaceSummary, trigger: HTMLElement) => void;
  buildingRun: PipelineRun | null;
  onResumeBuild: () => void;
};

export function WorkspaceList({
  workspaces,
  activeWorkspaceId,
  onSelectWorkspace,
  onOpenOptions,
  buildingRun,
  onResumeBuild,
}: WorkspaceListProps) {
  const activeRun = isRunActive(buildingRun) ? buildingRun : null;
  const runHasWorkspace = workspaces.some(
    (workspace) => workspace.workspace_id === buildingRun?.workspace_id,
  );
  // A failed run whose workspace never landed still needs a way back into the
  // creator, so it keeps its placeholder row.
  const placeholderRun =
    activeRun ?? (buildingRun?.status === "failed" && !runHasWorkspace ? buildingRun : null);

  return (
    <nav
      className="scrollbar-rt flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto px-2"
      aria-label="Workspaces"
    >
      {placeholderRun && !runHasWorkspace ? (
        <BuildPlaceholderRow run={placeholderRun} onResume={onResumeBuild} />
      ) : null}
      {workspaces.map((workspace) => {
        const selected = workspace.workspace_id === activeWorkspaceId;
        const building = activeRun?.workspace_id === workspace.workspace_id ? activeRun : null;
        return (
          <div
            key={workspace.workspace_id}
            className={cx(
              "group relative flex items-center gap-2 rounded-md px-2.5 py-2 transition-[background-color] duration-150",
              selected ? "bg-accent-subtle" : "hover:bg-[#e9ebed]",
            )}
          >
            <button
              className="min-w-0 flex-1 border-0 bg-transparent p-0 text-left"
              type="button"
              aria-current={selected ? "true" : undefined}
              onClick={() => onSelectWorkspace(workspace.workspace_id)}
            >
              <span
                className={cx(
                  "block truncate text-[13px] leading-[1.35]",
                  selected ? "font-semibold text-accent-deep" : "font-medium text-text-primary",
                )}
              >
                {workspace.title}
              </span>
              {building ? (
                <BuildingCaption run={building} selected={selected} />
              ) : (
                <span
                  className={cx(
                    "mt-0.5 block truncate text-[11px] leading-[1.35]",
                    selected ? "text-text-secondary" : "text-text-muted",
                  )}
                >
                  {workspaceCaption(workspace)}
                </span>
              )}
            </button>
            <button
              className={cx(
                "grid h-[22px] w-[22px] flex-none place-items-center rounded-[5px] border-0 bg-transparent p-0 opacity-0 transition-[background-color,color,opacity] duration-150 group-hover:opacity-100 focus-visible:opacity-100 aria-expanded:opacity-100",
                selected
                  ? "text-accent-deep hover:bg-accent-border"
                  : "text-text-muted hover:bg-[#dde0e3] hover:text-text-primary",
              )}
              type="button"
              onClick={(event) => onOpenOptions(workspace, event.currentTarget)}
              aria-label={`Options for ${workspace.title}`}
              title="Workspace options"
            >
              <EllipsisIcon className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
      {workspaces.length === 0 && !placeholderRun ? (
        <p className="mt-8 px-2.5 text-[11.5px] leading-[1.6] text-text-muted">
          Workspaces you build will live here.
        </p>
      ) : null}
    </nav>
  );
}

function BuildingCaption({ run, selected }: { run: PipelineRun; selected: boolean }) {
  const progress = buildProgress(run);
  return (
    <>
      <span
        className={cx(
          "mt-0.5 mb-1.5 block truncate text-[11px] leading-[1.35]",
          selected ? "text-text-secondary" : "text-text-muted",
        )}
      >
        Building{progress.currentLabel ? ` · ${lowerFirst(progress.currentLabel)}…` : "…"}
      </span>
      <ProgressBar percent={progress.percent} />
    </>
  );
}

function BuildPlaceholderRow({ run, onResume }: { run: PipelineRun; onResume: () => void }) {
  const failed = run.status === "failed";
  const progress = buildProgress(run);
  return (
    <button
      className="rounded-md px-2.5 py-2 text-left transition-[background-color] duration-150 hover:bg-[#e9ebed]"
      type="button"
      onClick={onResume}
    >
      <span className="block truncate text-[13px] font-medium leading-[1.35] text-text-primary">
        {run.topic}
      </span>
      <span
        className={cx(
          "mt-0.5 mb-1.5 block truncate text-[11px] leading-[1.35]",
          failed ? "text-error" : "text-text-muted",
        )}
      >
        {failed
          ? "Build failed"
          : `Building${progress.currentLabel ? ` · ${lowerFirst(progress.currentLabel)}…` : "…"}`}
      </span>
      {failed ? null : <ProgressBar percent={progress.percent} />}
    </button>
  );
}

function ProgressBar({ percent }: { percent: number }) {
  return (
    <span className="block h-[3px] overflow-hidden rounded-[2px] bg-[#dde0e3]" aria-hidden="true">
      <span
        className="block h-full rounded-[2px] bg-accent transition-[width] duration-500 ease-research"
        style={{ width: `${percent}%` }}
      />
    </span>
  );
}

function workspaceCaption(workspace: WorkspaceSummary): string {
  const updated = relativeTimestamp(workspace.updated_at);
  const papers = pluralize(workspace.paper_count, "paper");
  return updated ? `${papers} · updated ${updated}` : papers;
}

function lowerFirst(value: string): string {
  return value.charAt(0).toLowerCase() + value.slice(1);
}
