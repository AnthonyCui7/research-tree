import { cx } from "../../lib/cx";
import { secondaryActionClass } from "../../lib/controlClasses";
import { AccountDialog, AccountSection } from "./AccountDialog";

type SettingsDialogProps = {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  live: boolean;
  onRefresh: () => void;
  onClose: () => void;
};

/**
 * What this build can actually be set to. The account-level preferences the
 * design imagines — theme, defaults, notifications — need accounts to belong to;
 * until then, showing them switched off would be a lie about what they do.
 */
export function SettingsDialog({
  sidebarCollapsed,
  onToggleSidebar,
  live,
  onRefresh,
  onClose,
}: SettingsDialogProps) {
  return (
    <AccountDialog title="Settings" subtitle="Preferences for this browser." onClose={onClose}>
      <AccountSection title="Sidebar" detail="The workspace list down the left-hand side.">
        <Toggle
          checked={!sidebarCollapsed}
          label="Show the workspace sidebar"
          onChange={onToggleSidebar}
        />
      </AccountSection>

      <AccountSection
        title="Workspaces"
        detail={
          live
            ? "Changes on the server appear on their own."
            : "The update stream dropped, so the list is not refreshing itself."
        }
      >
        <div className="flex items-center gap-2.5">
          <button className={secondaryActionClass} type="button" onClick={onRefresh}>
            Reload workspaces
          </button>
          <span className="flex items-center gap-1.5 text-[12px] text-text-muted">
            <span
              className={cx("h-1.5 w-1.5 rounded-full", live ? "bg-accent" : "bg-text-muted")}
              aria-hidden="true"
            />
            {live ? "Live updates on" : "Live updates paused"}
          </span>
        </div>
      </AccountSection>

      <AccountSection title="Keyboard">
        <dl className="m-0 grid gap-2">
          <Shortcut keys="⌘K" label="Search branches and papers" />
          <Shortcut keys="esc" label="Close the open panel" />
          <Shortcut keys="Enter" label="Send an assistant message" />
        </dl>
      </AccountSection>
    </AccountDialog>
  );
}

function Toggle({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: () => void;
}) {
  return (
    <button
      className="flex w-full items-center gap-3 rounded-lg border border-border bg-surface px-3.5 py-2.5 text-left text-[13px] text-text-primary transition-[border-color] duration-150 hover:border-border-strong"
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
    >
      <span className="min-w-0 flex-1">{label}</span>
      <span
        className={cx(
          "relative h-[18px] w-8 flex-none rounded-full transition-[background-color] duration-150",
          checked ? "bg-accent" : "bg-border-strong",
        )}
        aria-hidden="true"
      >
        <span
          className={cx(
            "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-surface transition-[left] duration-150 ease-research",
            checked ? "left-[16px]" : "left-0.5",
          )}
        />
      </span>
    </button>
  );
}

function Shortcut({ keys, label }: { keys: string; label: string }) {
  return (
    <div className="flex items-center gap-2.5 text-[12.5px] text-text-secondary">
      <dt className="m-0">
        <kbd className="rounded-sm border border-border bg-surface px-[6px] py-px font-sans text-[11px] text-text-muted">
          {keys}
        </kbd>
      </dt>
      <dd className="m-0">{label}</dd>
    </div>
  );
}
