import { useEffect, useState } from "react";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import type { WorkspaceVersion } from "../../lib/types";

type WorkspaceHistoryProps = {
  open: boolean;
  workspaceId: string;
  workspaceTitle: string;
  currentVersionHash: string;
  versions: WorkspaceVersion[];
  onClose: () => void;
  onChanged: () => Promise<void>;
  onDeleted: () => Promise<void>;
};

export function WorkspaceHistory({
  open,
  workspaceId,
  workspaceTitle,
  currentVersionHash,
  versions,
  onClose,
  onChanged,
  onDeleted,
}: WorkspaceHistoryProps) {
  const [busyHash, setBusyHash] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [present, setPresent] = useState(open);
  const [closing, setClosing] = useState(false);
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
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);
  if (!present) return null;

  async function restore(versionHash: string) {
    if (versionHash === currentVersionHash) return;
    setBusyHash(versionHash);
    setError(null);
    try {
      await repositoryWorkspaceGateway.restoreWorkspace(workspaceId, versionHash, currentVersionHash);
      await onChanged();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusyHash(null);
    }
  }

  async function deleteWorkspace() {
    setBusyHash("delete");
    setError(null);
    try {
      await repositoryWorkspaceGateway.deleteWorkspace(workspaceId, currentVersionHash);
      await onDeleted();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setBusyHash(null);
    }
  }

  return (
    <aside className="utility-panel history-panel" data-state={closing ? "closing" : "open"} aria-label="Workspace history">
      <header>
        <div><h2>Version history</h2><span>{workspaceTitle}</span></div>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Close version history" title="Close">
          <svg aria-hidden="true" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"><path d="m4 4 8 8M12 4l-8 8" /></svg>
        </button>
      </header>
      <ol className="history-list">
        {[...versions].reverse().map((version) => (
          <li key={`${version.navigation_index ?? 0}:${version.version_hash}`} data-current={version.is_current || version.version_hash === currentVersionHash}>
            <div>
              <strong>{version.is_current || version.version_hash === currentVersionHash ? "Current state" : humanReason(version.reason)}</strong>
              <span>{formatDate(version.created_at)} · {actorLabel(version.actor_type || version.actor)}</span>
            </div>
            <button type="button" disabled={Boolean(busyHash) || version.version_hash === currentVersionHash} onClick={() => void restore(version.version_hash)}>
              {busyHash === version.version_hash ? "Restoring…" : version.version_hash === currentVersionHash ? "Current" : "Restore"}
            </button>
          </li>
        ))}
      </ol>
      <footer className="history-danger">
        {!confirmDelete ? (
          <button type="button" onClick={() => setConfirmDelete(true)}>Delete workspace</button>
        ) : (
          <div>
            <p>Delete “{workspaceTitle}”? You can recover it from local trash.</p>
            <button type="button" onClick={() => setConfirmDelete(false)}>Cancel</button>
            <button className="danger-action" type="button" disabled={busyHash === "delete"} onClick={() => void deleteWorkspace()}>
              {busyHash === "delete" ? "Deleting…" : "Delete"}
            </button>
          </div>
        )}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
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

function messageFrom(error: unknown): string {
  return "We could not complete that request. Please try again.";
}
