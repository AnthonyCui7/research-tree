import { MenuItem, MenuSection, PopoverMenu, type MenuAnchor } from "../ui/PopoverMenu";
import { BugIcon, GearIcon, HelpIcon, KeyIcon, SignOutIcon } from "../ui/icons";
import { Avatar, displayName } from "./Avatar";
import { isLocalSession } from "../../data/session";
import type { SessionInfo } from "../../lib/types";

/** The screens this menu opens; the shell owns which one is showing. */
export type AccountScreen = "settings" | "api-keys" | "help" | "report-bug";

type ProfileMenuProps = {
  anchor: MenuAnchor;
  session: SessionInfo;
  onClose: () => void;
  onOpenScreen: (screen: AccountScreen) => void;
  onSignOut: () => void;
  /** Last four characters of the configured key, or null when there is none. */
  apiKeyLabel: string | null;
};

/**
 * The account menu. Behind sign-in it shows the account the session belongs
 * to and ends that session; the local build shows its one implicit profile and
 * has no session to end.
 */
export function ProfileMenu({
  anchor,
  session,
  onClose,
  onOpenScreen,
  onSignOut,
  apiKeyLabel,
}: ProfileMenuProps) {
  const local = isLocalSession(session);
  const { user } = session;

  return (
    <PopoverMenu anchor={anchor} onClose={onClose} label="Account" width={256}>
      <div className="flex items-center gap-2.5 border-b border-hairline-soft px-3.5 py-3">
        <Avatar user={user} size={36} />
        <span className="min-w-0">
          <span className="block truncate text-[13.5px] font-bold text-text-primary">
            {displayName(user)}
          </span>
          <span className="block truncate text-[12px] text-text-muted">
            {local ? "Runs without accounts" : user.email}
          </span>
        </span>
      </div>

      <MenuSection>
        <MenuItem
          icon={<GearIcon className="h-[15px] w-[15px]" />}
          onClick={() => onOpenScreen("settings")}
        >
          Settings
        </MenuItem>
        <MenuItem
          icon={<KeyIcon className="h-[15px] w-[15px]" />}
          onClick={() => onOpenScreen("api-keys")}
          trailing={apiKeyLabel}
        >
          API keys
        </MenuItem>
        <MenuItem
          icon={<HelpIcon className="h-[15px] w-[15px]" />}
          onClick={() => onOpenScreen("help")}
        >
          Help &amp; docs
        </MenuItem>
        <MenuItem
          icon={<BugIcon className="h-[15px] w-[15px]" />}
          onClick={() => onOpenScreen("report-bug")}
        >
          Report a bug
        </MenuItem>
      </MenuSection>

      <MenuSection>
        <MenuItem
          tone="danger"
          icon={<SignOutIcon className="h-[15px] w-[15px]" />}
          disabled={local}
          title={local ? "This build runs without accounts, so there is no session to end" : undefined}
          onClick={onSignOut}
        >
          Sign out
        </MenuItem>
      </MenuSection>
    </PopoverMenu>
  );
}
