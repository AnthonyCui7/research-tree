import { WorkspaceList } from "../workspace/WorkspaceList";
import { Avatar, displayName } from "./Avatar";
import { cx } from "../../lib/cx";
import { kickerClass } from "../../lib/controlClasses";
import { ChevronUpDownIcon, PlusIcon, SidebarIcon } from "../ui/icons";
import type { PipelineRun, SessionUser, WorkspaceSummary } from "../../lib/types";

type SidebarProps = {
  collapsed: boolean;
  workspaces: WorkspaceSummary[];
  /** Null while the new-workspace page is showing. */
  activeWorkspaceId: string | null;
  homeActive: boolean;
  buildingRun: PipelineRun | null;
  /** False once the workspace event stream drops; the list stops self-updating. */
  live: boolean;
  user: SessionUser;
  /** The line under the account name: its email, or how the local build runs. */
  accountDetail: string;
  accountMenuOpen: boolean;
  onToggle: () => void;
  onNewWorkspace: () => void;
  onSelectWorkspace: (workspaceId: string) => void;
  onOpenOptions: (workspace: WorkspaceSummary, trigger: HTMLElement) => void;
  optionsOpenFor: string | null;
  onOpenAccount: (trigger: HTMLElement) => void;
};

/**
 * Navigation: a new workspace, the workspace list, and the account. Collapsed,
 * it keeps a rail of the same controls in the same places, so the account and
 * a new workspace stay one click away. On a narrow screen the open sidebar
 * covers the canvas instead of pushing it aside.
 */
export function Sidebar({
  collapsed,
  workspaces,
  activeWorkspaceId,
  homeActive,
  buildingRun,
  live,
  user,
  accountDetail,
  accountMenuOpen,
  onToggle,
  onNewWorkspace,
  onSelectWorkspace,
  onOpenOptions,
  optionsOpenFor,
  onOpenAccount,
}: SidebarProps) {
  // Everything stays rendered and fades with the width, so collapsing reads
  // as one motion; hidden is `invisible`, which also takes it out of the tab
  // order and away from assistive technology.
  const labelClass = cx(
    "transition-[opacity,visibility] duration-150",
    collapsed ? "invisible opacity-0" : "visible opacity-100",
  );

  return (
    <div
      className={cx(
        "relative h-full flex-none transition-[width] duration-200 ease-research",
        collapsed ? "w-14" : "w-[260px] max-[900px]:w-14",
      )}
    >
      {collapsed ? null : (
        <div
          className="fixed inset-0 z-backdrop hidden animate-backdrop-enter bg-[rgb(31_35_40_/_28%)] max-[900px]:block"
          role="presentation"
          onClick={onToggle}
        />
      )}
      <aside
        className={cx(
          "absolute inset-y-0 left-0 z-overlay flex flex-col overflow-hidden border-r border-hairline bg-sidebar transition-[width,box-shadow] duration-200 ease-research",
          collapsed ? "w-14" : "w-[260px] max-[900px]:shadow-popover",
        )}
        aria-label="Workspace navigation"
      >
        <div className="flex h-[52px] flex-none items-center gap-1.5 px-2.5">
          <button
            className="grid h-9 w-9 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-secondary transition-[background-color,color] duration-150 hover:bg-sidebar-hover hover:text-text-primary"
            type="button"
            onClick={onToggle}
            aria-label={collapsed ? "Show sidebar" : "Hide sidebar"}
            aria-expanded={!collapsed}
            title={collapsed ? "Show sidebar" : "Hide sidebar"}
          >
            <SidebarIcon className="h-4 w-4" />
          </button>
          <span
            className={cx(
              "truncate text-[14.5px] font-semibold tracking-[-0.01em] whitespace-nowrap text-text-primary",
              labelClass,
            )}
          >
            Research Tree
          </span>
        </div>

        <div className="flex-none px-2.5 pb-3">
          <button
            className={cx(
              "flex h-9 w-full items-center rounded-md border-0 text-left text-[13px] font-medium whitespace-nowrap transition-[background-color,color] duration-150",
              homeActive
                ? "bg-accent-subtle text-accent-deep"
                : "bg-transparent text-text-primary hover:bg-sidebar-hover",
            )}
            type="button"
            onClick={onNewWorkspace}
            aria-current={homeActive ? "page" : undefined}
            aria-label="New workspace"
            title={collapsed ? "New workspace" : undefined}
          >
            <span className="grid h-9 w-9 flex-none place-items-center" aria-hidden="true">
              <span className="grid h-[22px] w-[22px] place-items-center rounded-full bg-accent text-white shadow-[0_1px_2px_rgb(23_102_71/30%)]">
                <PlusIcon className="h-3 w-3" />
              </span>
            </span>
            <span className={cx("truncate", labelClass)} aria-hidden="true">
              New workspace
            </span>
          </button>
        </div>

        <div className={cx("flex min-h-0 flex-1 flex-col", labelClass)}>
          {workspaces.length > 0 || buildingRun || !live ? (
            <div className="flex flex-none items-center gap-2 px-5 pb-1.5 whitespace-nowrap">
              <span className={kickerClass}>Workspaces</span>
              {live ? null : (
                <span
                  className="flex items-center gap-1 text-[11px] text-text-muted"
                  role="status"
                  title="The workspace event stream dropped. Reconnecting…"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-text-muted" aria-hidden="true" />
                  Live updates paused
                </span>
              )}
            </div>
          ) : null}
          <WorkspaceList
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspaceId}
            onSelectWorkspace={onSelectWorkspace}
            onOpenOptions={onOpenOptions}
            optionsOpenFor={optionsOpenFor}
            buildingRun={buildingRun}
            onOpenBuild={onNewWorkspace}
          />
        </div>

        <div className="flex-none border-t border-hairline px-2.5 py-2">
          <button
            className="flex h-11 w-full items-center gap-1 rounded-md border-0 bg-transparent pr-2 text-left whitespace-nowrap transition-[background-color] duration-150 hover:bg-sidebar-hover aria-expanded:bg-sidebar-hover"
            type="button"
            onClick={(event) => onOpenAccount(event.currentTarget)}
            aria-haspopup="menu"
            aria-expanded={accountMenuOpen}
            aria-label="Account"
            title={collapsed ? displayName(user) : undefined}
          >
            <span className="grid h-9 w-9 flex-none place-items-center">
              <Avatar user={user} size={28} />
            </span>
            <span className={cx("min-w-0 flex-1", labelClass)}>
              <span className="block truncate text-[13px] font-medium text-text-primary">
                {displayName(user)}
              </span>
              <span className="block truncate text-[11.5px] text-text-muted">{accountDetail}</span>
            </span>
            <ChevronUpDownIcon className={cx("h-3.5 w-3.5 flex-none text-text-muted", labelClass)} />
          </button>
        </div>
      </aside>
    </div>
  );
}
