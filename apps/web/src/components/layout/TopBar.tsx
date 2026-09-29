import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { keycapClass } from "../../lib/controlClasses";
import { pluralize } from "../../lib/format";
import { ChatIcon, ClockIcon, SearchIcon } from "../ui/icons";

type TopBarProps = {
  workspaceTitle: string;
  branchCount: number | null;
  paperCount: number | null;
  onOpenSearch: () => void;
  searchDisabled: boolean;
  onToggleHistory: () => void;
  historyActive: boolean;
  historyDisabled: boolean;
  onToggleAssistant: () => void;
  assistantActive: boolean;
  assistantDisabled: boolean;
  /** Why the assistant cannot open right now, when it cannot. */
  assistantDisabledReason?: string;
};

/** The open workspace's header: what it is, and the tools that act on it. */
export function TopBar({
  workspaceTitle,
  branchCount,
  paperCount,
  onOpenSearch,
  searchDisabled,
  onToggleHistory,
  historyActive,
  historyDisabled,
  onToggleAssistant,
  assistantActive,
  assistantDisabled,
  assistantDisabledReason,
}: TopBarProps) {
  const counts =
    branchCount === null || paperCount === null
      ? null
      : `${pluralize(branchCount, "branch", "branches")} · ${pluralize(paperCount, "paper")}`;

  return (
    <header className="box-content flex h-13 flex-none items-center gap-2 border-b border-hairline bg-surface px-4">
      <div className="flex min-w-0 flex-1 items-baseline gap-3 overflow-hidden">
        <h1 className="m-0 truncate text-15 font-semibold text-text-primary">
          {workspaceTitle}
        </h1>
        {counts ? (
          <span className="flex-none text-13 whitespace-nowrap text-text-muted max-[720px]:hidden">
            {counts}
          </span>
        ) : null}
      </div>

      <button
        className="flex h-8 w-[min(260px,32vw)] min-w-0 flex-none items-center gap-2 rounded-md border border-transparent bg-surface-subtle pr-1.5 pl-3 text-13 text-text-muted transition-[background-color,border-color] duration-150 enabled:hover:border-border disabled:cursor-not-allowed disabled:opacity-60 max-[640px]:w-8 max-[640px]:justify-center max-[640px]:border-border max-[640px]:bg-surface max-[640px]:px-0"
        type="button"
        onClick={onOpenSearch}
        disabled={searchDisabled}
        aria-label="Search branches and papers"
        aria-keyshortcuts="Meta+K Control+K"
      >
        <SearchIcon className="size-4 flex-none" />
        <span className="min-w-0 flex-1 truncate text-left max-[640px]:hidden">Search this workspace</span>
        <kbd className={cx(keycapClass, "max-[640px]:hidden")}>
          ⌘K
        </kbd>
      </button>

      <ToolButton
        icon={<ClockIcon className="size-4" />}
        label="History"
        active={historyActive}
        disabled={historyDisabled}
        onClick={onToggleHistory}
      />
      <ToolButton
        icon={<ChatIcon className="size-4" />}
        label="Assistant"
        active={assistantActive}
        disabled={assistantDisabled}
        title={assistantDisabled ? assistantDisabledReason : undefined}
        onClick={onToggleAssistant}
        emphasis
      />
    </header>
  );
}

/** A panel toggle: pressed while its panel is the one open. */
function ToolButton({
  icon,
  label,
  active,
  disabled,
  title,
  onClick,
  emphasis = false,
}: {
  icon: ReactNode;
  label: string;
  active: boolean;
  disabled: boolean;
  title?: string;
  onClick: () => void;
  /** The assistant is the workspace's main tool, so its toggle carries the accent. */
  emphasis?: boolean;
}) {
  return (
    <button
      className={cx(
        "flex h-8 flex-none items-center gap-1.5 rounded-md border px-3 text-13 font-medium transition-[background-color,border-color,color] duration-150 disabled:cursor-not-allowed disabled:border-border disabled:bg-surface disabled:text-border-strong max-[640px]:px-2",
        active
          ? emphasis
            ? "border-accent-border bg-accent-subtle text-accent-deep"
            : "border-border-strong bg-surface-subtle text-text-primary"
          : emphasis
            ? "border-accent-border bg-surface text-accent-deep enabled:hover:bg-accent-wash"
            : "border-border bg-surface text-text-secondary enabled:hover:bg-surface-subtle enabled:hover:text-text-primary",
      )}
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      aria-label={label}
      title={title}
    >
      {icon}
      <span className="max-[640px]:hidden">{label}</span>
    </button>
  );
}
