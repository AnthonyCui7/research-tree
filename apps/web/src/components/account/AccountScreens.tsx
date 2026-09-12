import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { ApiKeysDialog } from "./ApiKeysDialog";
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
  return null;
}

/**
 * The masked key the account menu shows beside "API keys". Read once, and
 * again whenever `epoch` changes: the caller bumps it after the API keys
 * screen closes, the one place the key is saved or removed.
 */
export function useApiKeyLabel(epoch: number): string | null {
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
  }, [epoch]);

  return label;
}
