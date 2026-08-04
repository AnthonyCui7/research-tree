import { MenuItem, MenuSection, PopoverMenu, type MenuAnchor } from "../ui/PopoverMenu";
import { RefreshIcon, SidebarIcon } from "../ui/icons";

type ProfileMenuProps = {
  anchor: MenuAnchor;
  onClose: () => void;
  onRefresh: () => void;
  onToggleSidebar: () => void;
  sidebarCollapsed: boolean;
  live: boolean;
};

/**
 * The design's account menu, carrying what this build actually has: a local
 * profile, the state of the live workspace stream, and the shortcuts.
 */
export function ProfileMenu({
  anchor,
  onClose,
  onRefresh,
  onToggleSidebar,
  sidebarCollapsed,
  live,
}: ProfileMenuProps) {
  return (
    <PopoverMenu anchor={anchor} onClose={onClose} label="Account" width={244}>
      <div className="flex items-center gap-2.5 border-b border-hairline-soft px-3.5 py-3">
        <span
          className="grid h-8 w-8 flex-none place-items-center rounded-full bg-accent-subtle text-xs font-semibold text-accent-deep"
          aria-hidden="true"
        >
          RT
        </span>
        <span className="min-w-0">
          <span className="block text-[13px] font-semibold text-text-primary">Local profile</span>
          <span className="block truncate text-[11px] text-text-muted">
            Accounts are not part of the local build
          </span>
        </span>
      </div>

      <MenuSection>
        <MenuItem icon={<RefreshIcon className="h-[13px] w-[13px]" />} onClick={onRefresh}>
          Reload workspaces
        </MenuItem>
        <MenuItem icon={<SidebarIcon className="h-[13px] w-[13px]" />} onClick={onToggleSidebar}>
          {sidebarCollapsed ? "Show sidebar" : "Hide sidebar"}
        </MenuItem>
      </MenuSection>

      <div className="grid gap-1.5 px-3.5 py-3">
        <span className="text-[10px] font-semibold tracking-[0.06em] text-text-muted uppercase">
          Shortcuts
        </span>
        <ShortcutRow keys="⌘K" label="Search this workspace" />
        <ShortcutRow keys="esc" label="Close the open panel" />
        <span className="mt-1 flex items-center gap-1.5 text-[11px] text-text-muted">
          <span
            className={live ? "h-1.5 w-1.5 rounded-full bg-accent" : "h-1.5 w-1.5 rounded-full bg-text-muted"}
            aria-hidden="true"
          />
          {live ? "Live updates on" : "Live updates paused"}
        </span>
      </div>
    </PopoverMenu>
  );
}

function ShortcutRow({ keys, label }: { keys: string; label: string }) {
  return (
    <span className="flex items-center gap-2 text-[11.5px] text-text-secondary">
      <kbd className="rounded-sm border border-border bg-surface px-[5px] py-px font-sans text-[10.5px] text-text-muted">
        {keys}
      </kbd>
      {label}
    </span>
  );
}
