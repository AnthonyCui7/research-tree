import { useEffect, useState, type ReactNode } from "react";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../../lib/apiError";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { cx } from "../../lib/cx";
import { dateTimeLabel } from "../../lib/format";
import { badgeClass, compactActionClass, errorNoticeClass } from "../../lib/controlClasses";
import { PanelHeader } from "../panel/RightPanel";
import { ChatIcon, PencilIcon, TreeIcon } from "../ui/icons";
import type { WorkspaceVersion } from "../../lib/types";

type WorkspaceHistoryProps = {
  workspaceId: string;
  currentVersionHash: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
};

export function WorkspaceHistory({
  workspaceId,
  currentVersionHash,
  onClose,
  onChanged,
}: WorkspaceHistoryProps) {
  const [versions, setVersions] = useState<WorkspaceVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyHash, setBusyHash] = useState<string | null>(null);
  // The hash a landed restore replaced. `onChanged` resolves when the
  // summaries land and the document follows a beat later, so until the hash
  // on screen has moved past this one a second restore would be sent against
  // the version the server just replaced.
  const [replacedHash, setReplacedHash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (replacedHash !== null && currentVersionHash !== replacedHash) setReplacedHash(null);
  }, [currentVersionHash, replacedHash]);

  // The version list is read by this panel alone, so it loads when the panel
  // opens. The current hash keys it: any change produces a new hash and a
  // fresh list.
  useEffect(() => {
    let active = true;
    setLoading(true);
    void (async () => {
      try {
        const loaded = await repositoryWorkspaceGateway.getWorkspaceVersions(workspaceId);
        if (active) setVersions(loaded);
      } catch (requestError) {
        if (active) setError(messageFrom(requestError));
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [currentVersionHash, workspaceId]);

  // A restore stays pending until the document it produced is on screen, so
  // the next restore cannot be sent against a version the server already
  // replaced. A reload that fails ends the wait too; the list keeps working.
  async function restore(versionHash: string) {
    if (versionHash === currentVersionHash) return;
    setBusyHash(versionHash);
    setError(null);
    try {
      const result = await repositoryWorkspaceGateway.restoreWorkspace(
        workspaceId,
        versionHash,
        currentVersionHash,
      );
      if (result.changed) setReplacedHash(currentVersionHash);
      await onChanged();
    } catch (requestError) {
      setReplacedHash(null);
      const conflict = isVersionConflict(requestError);
      setError(conflict ? VERSION_CONFLICT_MESSAGE : messageFrom(requestError));
      if (conflict) {
        // The restore was aimed at a hash the server has already replaced.
        // Reloading is what makes the next attempt valid.
        await onChanged().catch(() => undefined);
      }
    } finally {
      setBusyHash(null);
    }
  }

  const ordered = [...versions].reverse();

  return (
    <>
      <PanelHeader title="Version history" onClose={onClose} closeLabel="Close version history" />
      <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {error ? (
          <p className={cx(errorNoticeClass, "mt-0 mb-4")} role="alert">
            {error}
          </p>
        ) : null}
        {ordered.length === 0 ? (
          <p className="m-0 text-13 text-text-muted">
            {loading ? "Loading versions…" : "No saved versions yet."}
          </p>
        ) : null}
        <ol className="m-0 list-none p-0">
          {ordered.map((version, index) => {
            // The server marks exactly one entry current, by position. Matching
            // on the hash marked both halves of a duplicate that older
            // workspaces can still carry, so neither offered a way back.
            const current = version.is_current ?? version.version_hash === currentVersionHash;
            const last = index === ordered.length - 1;
            const editor = versionEditor(version);
            return (
              <li
                className="relative flex gap-3 pb-6 last:pb-0"
                key={`${version.navigation_index ?? 0}:${version.version_hash}`}
              >
                {last ? null : (
                  <span
                    className="absolute top-8 bottom-1 left-[13.5px] w-px bg-hairline"
                    aria-hidden="true"
                  />
                )}
                <span
                  className={cx(
                    "relative grid h-7 w-7 flex-none place-items-center rounded-full",
                    current
                      ? "bg-accent text-white shadow-[0_0_0_4px_var(--color-accent-subtle)]"
                      : "border border-hairline bg-surface text-text-muted",
                  )}
                  aria-hidden="true"
                >
                  {editor.icon}
                </span>
                <div className="min-w-0 flex-1 pt-1">
                  <div className="flex items-start gap-2">
                    <p className="m-0 flex-1 text-13 font-medium text-text-primary [overflow-wrap:anywhere]">
                      {humanReason(version.reason)}
                    </p>
                    {current ? (
                      <span className={cx(badgeClass, "bg-accent-subtle text-accent-deep")}>Current</span>
                    ) : null}
                  </div>
                  <p className="mt-1.5 mb-0 text-12 text-text-muted text-trim">
                    {editor.label} · {dateTimeLabel(version.created_at)}
                  </p>
                  {current ? null : (
                    <div className="mt-3 flex items-center gap-3">
                      <button
                        className={compactActionClass}
                        type="button"
                        disabled={busyHash !== null || replacedHash !== null}
                        onClick={() => void restore(version.version_hash)}
                      >
                        {busyHash === version.version_hash ? "Restoring…" : "Restore"}
                      </button>
                      <span className="font-mono text-11 text-text-muted">
                        {version.version_hash.slice(0, 7)}
                      </span>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </div>
    </>
  );
}

function humanReason(reason: string): string {
  const normalized = reason.trim();
  if (!normalized) return "Manual revision";
  // A build publishes twice: the tree as soon as it can be read, then the
  // finished workspace with its similar papers.
  if (normalized.startsWith("pipeline run ")) {
    return normalized.endsWith(" completed") ? "Finished build" : "Generated structure";
  }
  if (normalized === "restored from workspace history") return "Restored structure";
  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return label.length > 96 ? `${label.slice(0, 93).trimEnd()}…` : label;
}

/** Who made the version: the reader, the assistant with the reader's approval, or a build. */
function versionEditor(version: WorkspaceVersion): { label: string; icon: ReactNode } {
  const actor = version.actor_type || version.actor;
  if (actor === "user") return { label: "You", icon: <PencilIcon className="size-4" /> };
  if (actor === "agent") {
    return { label: "Assistant, approved by you", icon: <ChatIcon className="size-4" /> };
  }
  return { label: "Build", icon: <TreeIcon className="size-4" /> };
}
