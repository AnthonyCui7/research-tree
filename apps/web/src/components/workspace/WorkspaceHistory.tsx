import { useEffect, useState } from "react";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../../lib/apiError";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { cx } from "../../lib/cx";
import { dateTimeLabel } from "../../lib/format";
import { compactActionClass } from "../../lib/controlClasses";
import { PanelHeader } from "../panel/RightPanel";
import { ClockIcon } from "../ui/icons";
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
  const [error, setError] = useState<string | null>(null);

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

  // A restore stays pending until the reload after it settles, so the next
  // restore cannot be sent against a version the server already replaced.
  // A reload that fails ends the wait too; the list keeps working.
  async function restore(versionHash: string) {
    if (versionHash === currentVersionHash) return;
    setBusyHash(versionHash);
    setError(null);
    try {
      await repositoryWorkspaceGateway.restoreWorkspace(workspaceId, versionHash, currentVersionHash);
      await onChanged();
    } catch (requestError) {
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
      <PanelHeader
        title="Version history"
        icon={<ClockIcon className="h-3.5 w-3.5" />}
        onClose={onClose}
        closeLabel="Close version history"
      />
      <div className="scrollbar-rt min-h-0 flex-1 overflow-y-auto px-3 py-3.5">
        {error ? (
          <p className="mb-3 rounded-lg border border-error-border bg-error-surface px-[13px] py-2.5 text-xs leading-[1.5] text-error" role="alert">
            {error}
          </p>
        ) : null}
        {ordered.length === 0 ? (
          <p className="px-2.5 py-3.5 text-xs text-text-muted">
            {loading ? "Loading versions…" : "No saved versions yet."}
          </p>
        ) : null}
        <ol className="m-0 flex list-none flex-col gap-0.5 p-0">
          {ordered.map((version) => {
            // The server marks exactly one entry current, by position. Matching
            // on the hash marked both halves of a duplicate that older
            // workspaces can still carry, so neither offered a way back.
            const current = version.is_current ?? version.version_hash === currentVersionHash;
            return (
              <li
                className={cx("rounded-[9px] px-2.5 py-3", current && "bg-accent-wash")}
                key={`${version.navigation_index ?? 0}:${version.version_hash}`}
              >
                <div className="flex items-start gap-2">
                  <p className="m-0 flex-1 text-[12.5px] font-semibold leading-[1.45] text-text-primary [overflow-wrap:anywhere]">
                    {humanReason(version.reason)}
                  </p>
                  {current ? (
                    <span className="flex-none rounded-[5px] bg-accent-subtle px-2 py-0.5 text-[10px] font-semibold tracking-[0.02em] text-accent-deep uppercase">
                      Current
                    </span>
                  ) : null}
                </div>
                <p className="mt-[3px] mb-0 text-[11px] leading-[1.5] text-text-muted">
                  {dateTimeLabel(version.created_at)}
                </p>
                <p className="mt-px mb-0 text-[11px] leading-[1.5] text-text-muted">
                  {editorLabel(version)}
                </p>
                {!current ? (
                  <div className="mt-2 flex items-center gap-1.5">
                    <button
                      className={compactActionClass}
                      type="button"
                      disabled={busyHash !== null}
                      onClick={() => void restore(version.version_hash)}
                    >
                      {busyHash === version.version_hash ? "Restoring…" : "Restore"}
                    </button>
                    <span className="font-mono text-[10px] text-text-muted">
                      {version.version_hash.slice(0, 7)}
                    </span>
                  </div>
                ) : null}
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
  if (normalized.startsWith("pipeline run ")) return "Generated structure";
  if (normalized === "restored from workspace history") return "Restored structure";
  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return label.length > 96 ? `${label.slice(0, 93).trimEnd()}…` : label;
}

function editorLabel(version: WorkspaceVersion): string {
  const actor = version.actor_type || version.actor;
  if (actor === "user") return "Editor: You";
  if (actor === "agent") return "Editors: You + Assistant";
  return "Editor: Pipeline";
}
