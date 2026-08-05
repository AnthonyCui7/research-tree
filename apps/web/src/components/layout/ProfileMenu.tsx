import { MenuItem, MenuSection, PopoverMenu, type MenuAnchor } from "../ui/PopoverMenu";
import { BugIcon, GearIcon, HelpIcon, KeyIcon, SignOutIcon } from "../ui/icons";

/** The screens this menu opens; the shell owns which one is showing. */
export type AccountScreen =
  | "settings"
  | "api-keys"
  | "help"
  | "report-bug"
  | "sign-in"
  | "create-account";

type ProfileMenuProps = {
  anchor: MenuAnchor;
  onClose: () => void;
  onOpenScreen: (screen: AccountScreen) => void;
  /** Last four characters of the configured key, or null when there is none. */
  apiKeyLabel: string | null;
};

/**
 * The account menu. There are no accounts in this build — no auth, no database —
 * so the identity is the local one and signing out is shown but inert rather
 * than invented; see PROJECT.md "Next Steps".
 */
export function ProfileMenu({ anchor, onClose, onOpenScreen, apiKeyLabel }: ProfileMenuProps) {
  return (
    <PopoverMenu anchor={anchor} onClose={onClose} label="Account" width={256}>
      <div className="flex items-center gap-2.5 border-b border-hairline-soft px-3.5 py-3">
        <span
          className="grid h-9 w-9 flex-none place-items-center rounded-full bg-accent-subtle text-[13px] font-bold text-accent-deep"
          aria-hidden="true"
        >
          RT
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[13.5px] font-bold text-text-primary">
            Local profile
          </span>
          <span className="block truncate text-[12px] text-text-muted">
            Signed in on this device
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
          icon={<SignOutIcon className="h-[15px] w-[15px] -scale-x-100" />}
          onClick={() => onOpenScreen("sign-in")}
        >
          Sign in / create account
        </MenuItem>
        <MenuItem
          tone="danger"
          icon={<SignOutIcon className="h-[15px] w-[15px]" />}
          disabled
          title="There is no account to sign out of in this build"
          onClick={() => {}}
        >
          Sign out
        </MenuItem>
      </MenuSection>
    </PopoverMenu>
  );
}
