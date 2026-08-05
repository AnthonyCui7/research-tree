import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { ApiKeysDialog } from "./ApiKeysDialog";
import { AuthScreens } from "./AuthScreens";
import { HelpDialog } from "./HelpDialog";
import { ReportBugDialog } from "./ReportBugDialog";
import { SettingsDialog } from "./SettingsDialog";
import type { AccountScreen } from "../layout/ProfileMenu";

type AccountScreensProps = {
  screen: AccountScreen | null;
  onClose: () => void;
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
  live: boolean;
  onRefresh: () => void;
};

/** One place the shell mounts whichever account screen the menu asked for. */
export function AccountScreens({
  screen,
  onClose,
  sidebarCollapsed,
  onToggleSidebar,
  live,
  onRefresh,
}: AccountScreensProps) {
  if (screen === "settings") {
    return (
      <SettingsDialog
        sidebarCollapsed={sidebarCollapsed}
        onToggleSidebar={onToggleSidebar}
        live={live}
        onRefresh={onRefresh}
        onClose={onClose}
      />
    );
  }
  if (screen === "api-keys") {
    return <ApiKeysDialog onClose={onClose} />;
  }
  if (screen === "help") {
    return <HelpDialog onClose={onClose} />;
  }
  if (screen === "report-bug") {
    return <ReportBugDialog onClose={onClose} />;
  }
  if (screen === "sign-in" || screen === "create-account") {
    // The mode is the starting point only; the screen swaps between the two
    // itself, so remounting on every toggle would lose the typed email.
    return <AuthScreens key={screen} initialMode={screen} onClose={onClose} />;
  }
  return null;
}

/**
 * The masked key the account menu shows beside "API keys". Read once per
 * session: it comes from the server's environment, which cannot change while
 * the page is open.
 */
export function useApiKeyLabel(): string | null {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    repositoryWorkspaceGateway
      .getApiKeys()
      .then((keys) => {
        if (!cancelled) setLabel(keys.openai.masked);
      })
      // A key that cannot be read is simply not labelled; the screen behind the
      // menu item reports the failure properly.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  return label;
}
