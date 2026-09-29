import { useState } from "react";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../../lib/apiError";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { DIALOG_EXIT_MS } from "../../lib/animation";
import { useModalDialog } from "../../lib/modalDialog";
import { cx } from "../../lib/cx";
import { dangerActionClass, errorNoticeClass, secondaryActionClass } from "../../lib/controlClasses";
import type { WorkspaceSummary } from "../../lib/types";

type DeleteWorkspaceDialogProps = {
  workspace: WorkspaceSummary;
  onCancel: () => void;
  onDeleted: () => Promise<void>;
  onChanged: () => Promise<void>;
};

export function DeleteWorkspaceDialog({
  workspace,
  onCancel,
  onDeleted,
  onChanged,
}: DeleteWorkspaceDialogProps) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { ref, closing, dismiss } = useModalDialog(DIALOG_EXIT_MS);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await repositoryWorkspaceGateway.deleteWorkspace(
        workspace.workspace_id,
        workspace.workspace_version_hash,
      );
      await onDeleted();
    } catch (requestError) {
      if (isVersionConflict(requestError)) {
        // The workspace changed underneath the request; it deserves a fresh
        // look before it is confirmed again.
        setError(VERSION_CONFLICT_MESSAGE);
        await onChanged();
      } else {
        setError(messageFrom(requestError));
      }
      setBusy(false);
    }
  }

  // A confirmation that cannot be escaped is a trap; a delete already underway
  // is not something Escape should appear to undo.
  return (
    <dialog
      ref={ref}
      className={cx(
        "fixed inset-0 m-0 grid h-full max-h-none w-full max-w-none place-items-center border-0 bg-transparent p-6 text-text-primary outline-none",
        closing ? "[&::backdrop]:animate-backdrop-exit" : "[&::backdrop]:animate-backdrop-enter",
      )}
      tabIndex={-1}
      role="alertdialog"
      aria-labelledby="delete-workspace-title"
      aria-describedby="delete-workspace-detail"
      onClose={onCancel}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) dismiss();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key !== "Escape") return;
        event.preventDefault();
        if (!busy) dismiss();
      }}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) dismiss();
      }}
    >
      <section
        className={cx(
          "w-full max-w-[440px] overflow-hidden rounded-2xl bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
      >
        <div className="p-6">
          <h2
            className="m-0 text-17 font-semibold text-text-primary [overflow-wrap:anywhere]"
            id="delete-workspace-title"
          >
            Delete “{workspace.title}”?
          </h2>
          <p
            className="mt-2 mb-0 text-14 text-text-secondary"
            id="delete-workspace-detail"
          >
            This removes the workspace and its version history from Research Tree.
          </p>
          {error ? (
            <p className={cx(errorNoticeClass, "mt-4 mb-0")} role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex items-center justify-end gap-2 px-6 pb-6">
          <button className={secondaryActionClass} type="button" onClick={dismiss} disabled={busy}>
            Cancel
          </button>
          <button className={dangerActionClass} type="button" onClick={() => void remove()} disabled={busy}>
            {busy ? "Deleting…" : "Delete workspace"}
          </button>
        </div>
      </section>
    </dialog>
  );
}
