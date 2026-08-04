import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../../lib/apiError";
import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { cx } from "../../lib/cx";
import type { WorkspaceVersion } from "../../lib/types";

type WorkspaceHistoryProps = {
  open: boolean;
  workspaceId: string;
  workspaceTitle: string;
  currentVersionHash: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
  onDeleted: () => Promise<void>;
};

export function WorkspaceHistory({
  open,
  workspaceId,
  workspaceTitle,
  currentVersionHash,
  onClose,
  onChanged,
  onDeleted,
}: WorkspaceHistoryProps) {
  const [busyHash, setBusyHash] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
  const [versions, setVersions] = useState<WorkspaceVersion[]>([]);
  const [loadingVersions, setLoadingVersions] = useState(false);
  useEffect(() => {
    if (open) {
      setPresent(true);
      setClosing(false);
      return;
    }
    if (!present) return;
    setClosing(true);
    const timer = window.setTimeout(() => setPresent(false), 200);
    return () => window.clearTimeout(timer);
  }, [open, present]);
  useEffect(() => {
    if (open) return;
    setConfirmDelete(false);
    setError(null);
  }, [open]);
  // A restore stays pending until the refreshed hash arrives, so the next
  // restore cannot be sent against a version the server already replaced.
  useEffect(() => {
    setBusyHash(null);
  }, [currentVersionHash]);
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);
  // Nothing outside this panel reads the version list, so it is fetched when
  // the panel opens instead of on every workspace load. The current hash keys
  // it: any change to the workspace produces a new hash and a fresh list.
  useEffect(() => {
    if (!open) return;
    let active = true;
    setLoadingVersions(true);
    void (async () => {
      try {
        const loaded = await repositoryWorkspaceGateway.getWorkspaceVersions(workspaceId);
        if (active) setVersions(loaded);
      } catch (requestError) {
        if (active) setError(messageFrom(requestError));
      } finally {
        if (active) setLoadingVersions(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [currentVersionHash, open, workspaceId]);
  if (!present) return null;

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
      setBusyHash(null);
      if (conflict) {
        // The restore was aimed at a hash the server has already replaced.
        // Reloading is what makes the next attempt valid.
        await onChanged();
      }
    }
  }

  async function deleteWorkspace() {
    setBusyHash("delete");
    setError(null);
    try {
      await repositoryWorkspaceGateway.deleteWorkspace(workspaceId, currentVersionHash);
      await onDeleted();
    } catch (requestError) {
      const conflict = isVersionConflict(requestError);
      setError(conflict ? VERSION_CONFLICT_MESSAGE : messageFrom(requestError));
      if (conflict) {
        // Deleting a workspace that changed underneath the request deserves a
        // fresh look before it is confirmed again.
        setConfirmDelete(false);
        await onChanged();
      }
    } finally {
      setBusyHash(null);
    }
  }

  return (
    <aside className="fixed top-16 right-0 bottom-0 z-panel flex h-auto w-[min(420px,calc(100%_-_80px))] animate-interface-right-enter flex-col border-l border-border bg-surface shadow-panel-left data-[state=closing]:pointer-events-none data-[state=closing]:animate-interface-right-exit max-[980px]:top-auto max-[980px]:left-[68px] max-[980px]:h-[min(72vh,680px)] max-[980px]:w-auto max-[980px]:border-l-0 max-[980px]:border-t max-[720px]:left-0 max-[720px]:h-[min(80vh,720px)]" data-state={closing ? "closing" : "open"} aria-label="Workspace history">
      <header className="flex min-w-0 items-center justify-between gap-[18px] border-b border-border px-6 pt-[21px] pb-[18px] max-[720px]:p-[18px]">
        <div className="grid min-w-0 gap-[5px]"><h2 className="m-0 text-balance text-base font-bold leading-tight tracking-normal text-text-primary [overflow-wrap:anywhere]">Version history</h2><span className="text-[11px] font-semibold text-text-secondary">{workspaceTitle}</span></div>
        <button className="grid h-8 w-8 flex-none place-items-center rounded-md border-0 bg-transparent p-0 text-text-secondary transition-[background-color,border-color,color,transform] duration-200 ease-research enabled:hover:bg-surface-subtle enabled:hover:text-text-primary enabled:active:scale-[0.94] disabled:cursor-not-allowed disabled:text-text-muted [&_svg]:h-[18px] [&_svg]:w-[18px] max-[720px]:h-10 max-[720px]:w-10" type="button" onClick={onClose} aria-label="Close version history" title="Close">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
        </button>
      </header>
      <ol className="scrollbar-rt m-0 min-h-0 flex-1 list-none overflow-y-auto px-6 pt-2 max-[720px]:px-[18px]">
        {versions.length === 0 ? (
          <li className="py-[15px] text-xs text-text-secondary">
            {loadingVersions ? "Loading versions…" : "No saved versions yet."}
          </li>
        ) : null}
        {[...versions].reverse().map((version) => (
          <li className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-border py-[15px]" key={`${version.navigation_index ?? 0}:${version.version_hash}`}>
            <div className="grid gap-1">
              <strong className={cx("text-[13px] leading-[1.35]", version.version_hash === currentVersionHash && "text-accent-deep")}>{version.version_hash === currentVersionHash ? "Current state" : humanReason(version.reason)}</strong>
              <span className="text-[11px] text-text-secondary">{formatDate(version.created_at)} · {actorLabel(version.actor_type || version.actor)}</span>
            </div>
            <button className="min-h-[34px] rounded-sm border border-border-strong bg-surface px-2.5 py-[7px] text-xs font-semibold text-text-primary enabled:hover:border-accent enabled:hover:text-accent-deep disabled:text-text-secondary disabled:opacity-65" type="button" disabled={Boolean(busyHash) || version.version_hash === currentVersionHash} onClick={() => void restore(version.version_hash)}>
              {busyHash === version.version_hash ? "Restoring…" : version.version_hash === currentVersionHash ? "Current" : "Restore"}
            </button>
          </li>
        ))}
      </ol>
      <footer className="border-t border-border p-4 px-6 max-[720px]:px-[18px]">
        {!confirmDelete ? (
          <button className="min-h-[34px] rounded-sm border border-border-strong bg-surface px-2.5 py-[7px] text-xs font-semibold text-text-primary enabled:hover:border-accent enabled:hover:text-accent-deep" type="button" disabled={Boolean(busyHash)} onClick={() => setConfirmDelete(true)}>Delete workspace</button>
        ) : (
          <div className="flex flex-wrap justify-end gap-2">
            <p className="m-0 mb-1.5 w-full text-xs leading-[1.45] text-text-secondary">Delete “{workspaceTitle}”? This removes the workspace from Research Tree.</p>
            <button className="min-h-[34px] rounded-sm border border-border-strong bg-surface px-2.5 py-[7px] text-xs font-semibold text-text-primary enabled:hover:border-accent enabled:hover:text-accent-deep" type="button" onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button className="min-h-[34px] rounded-sm border border-error bg-error px-2.5 py-[7px] text-xs font-semibold text-surface" type="button" disabled={Boolean(busyHash)} onClick={() => void deleteWorkspace()}>
              {busyHash === "delete" ? "Deleting…" : "Delete"}
            </button>
          </div>
        )}
        {error ? <p className="m-0 mt-3 rounded-sm bg-[color-mix(in_srgb,var(--color-error)_9%,var(--color-surface))] px-3 py-2.5 text-xs leading-[1.45] text-error" role="alert">{error}</p> : null}
      </footer>
    </aside>
  );
}

function humanReason(reason: string): string {
  const normalized = reason.trim();
  if (!normalized) return "Manual revision";
  if (normalized.startsWith("pipeline run ")) return "Generated structure";
  if (normalized === "restored from workspace history") return "Restored structure";
  const label = normalized.charAt(0).toUpperCase() + normalized.slice(1);
  return label.length > 72 ? `${label.slice(0, 69).trimEnd()}…` : label;
}

function actorLabel(actor: string): string {
  if (actor === "user") return "You";
  if (actor === "agent") return "Assistant";
  return "System";
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "Unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unknown date";
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}
