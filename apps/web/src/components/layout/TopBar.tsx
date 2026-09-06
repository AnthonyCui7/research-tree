import { cx } from "../../lib/cx";
import { pluralize } from "../../lib/format";
import { outlineIconButtonClass } from "../../lib/controlClasses";
import { ClockIcon, SearchIcon, SidebarIcon } from "../ui/icons";
import { Avatar } from "./Avatar";
import type { SessionUser } from "../../lib/types";

type TopBarProps = {
  workspaceTitle: string;
  branchCount: number | null;
  paperCount: number | null;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  onOpenSearch: () => void;
  searchDisabled: boolean;
  onToggleHistory: () => void;
  historyActive: boolean;
  historyDisabled: boolean;
  onToggleProfile: (trigger: HTMLElement) => void;
  profileOpen: boolean;
  user: SessionUser;
};

export function TopBar({
  workspaceTitle,
  branchCount,
  paperCount,
  sidebarCollapsed,
  onToggleSidebar,
  onOpenSearch,
  searchDisabled,
  onToggleHistory,
  historyActive,
  historyDisabled,
  onToggleProfile,
  profileOpen,
  user,
}: TopBarProps) {
  const counts =
    branchCount === null || paperCount === null
      ? null
      : `${pluralize(branchCount, "branch", "branches")} · ${pluralize(paperCount, "paper")}`;

  return (
    <header className="flex h-[52px] flex-none items-center gap-2.5 border-b border-border bg-surface px-3.5">
      {sidebarCollapsed ? (
        <button
          className={outlineIconButtonClass}
          type="button"
          onClick={onToggleSidebar}
          aria-label="Show workspace sidebar"
          title="Open sidebar"
        >
          <SidebarIcon className="h-3.5 w-3.5" />
        </button>
      ) : null}

      <div className="flex max-w-[32%] min-w-0 flex-none items-baseline gap-2 overflow-hidden">
        <span className="truncate text-sm font-semibold tracking-[-0.01em] text-text-primary">
          {workspaceTitle}
        </span>
        {counts ? (
          <span className="min-w-0 truncate text-[11.5px] whitespace-nowrap text-text-muted max-[720px]:hidden">
            {counts}
          </span>
        ) : null}
      </div>

      <div className="flex min-w-0 flex-1 justify-center overflow-hidden">
        <button
          className="flex w-[min(340px,100%)] min-w-0 items-center gap-2 overflow-hidden rounded-md border border-transparent bg-surface-subtle px-2.5 py-1.5 text-[12.5px] text-text-muted transition-[background-color,border-color] duration-150 enabled:hover:border-border enabled:hover:bg-[#eef0f2] disabled:cursor-not-allowed disabled:opacity-60"
          type="button"
          onClick={onOpenSearch}
          disabled={searchDisabled}
        >
          <SearchIcon className="h-[13px] w-[13px] flex-none" />
          <span className="min-w-0 flex-1 truncate text-left">Search branches and papers…</span>
          <kbd className="flex-none rounded-sm border border-border bg-surface px-[5px] py-px font-sans text-[10.5px] text-text-muted max-[520px]:hidden">
            ⌘K
          </kbd>
        </button>
      </div>

      <button
        className={cx(
          "flex flex-none items-center gap-1.5 rounded-[7px] border px-3 py-1.5 text-[12.5px] font-semibold transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed disabled:border-border disabled:text-border-strong",
          historyActive
            ? "border-border-strong bg-surface-subtle text-text-primary"
            : "border-border bg-surface text-text-secondary enabled:hover:bg-surface-subtle enabled:hover:text-text-primary",
        )}
        type="button"
        onClick={onToggleHistory}
        disabled={historyDisabled}
        aria-pressed={historyActive}
      >
        <ClockIcon className="h-[13px] w-[13px]" />
        <span className="max-[640px]:hidden">History</span>
      </button>

      <span className="h-5 w-px flex-none bg-border" aria-hidden="true" />

      <button
        className="grid h-[30px] w-[30px] flex-none place-items-center rounded-full border-0 bg-transparent p-0 transition-[box-shadow] duration-150 hover:shadow-[0_0_0_2px_var(--color-accent-border)] aria-expanded:shadow-[0_0_0_2px_var(--color-accent-border)]"
        type="button"
        onClick={(event) => onToggleProfile(event.currentTarget)}
        aria-haspopup="menu"
        aria-expanded={profileOpen}
        aria-label="Account"
        title="Account"
      >
        <Avatar user={user} size={30} />
      </button>
    </header>
  );
}
