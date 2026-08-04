import { useEffect, useState } from "react";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../../lib/apiError";
import { repositoryWorkspaceGateway } from "../../data/workspaceApi";
import { DIALOG_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { cx } from "../../lib/cx";
import { dangerActionClass, secondaryActionClass } from "../../lib/controlClasses";
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
  const { closing, dismiss } = useDismissAnimation(onCancel, DIALOG_EXIT_MS);

  // A confirmation that cannot be escaped is a trap; a delete already underway
  // is not something Escape should appear to undo.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        event.stopPropagation();
        dismiss();
      }
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [busy, dismiss]);

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

  return (
    <div
      className={cx(
        "fixed inset-0 z-backdrop grid place-items-center bg-[rgb(31_35_40_/_30%)] p-6",
        closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
      )}
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) dismiss();
      }}
    >
      <section
        className={cx(
          "w-full max-w-[440px] overflow-hidden rounded-[13px] bg-surface shadow-dialog",
          closing ? "animate-interface-center-exit" : "animate-interface-center-enter",
        )}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-workspace-title"
      >
        <div className="px-6 pt-[22px] pb-5">
          <h2
            className="m-0 text-[17px] font-bold tracking-[-0.01em] text-text-primary"
            id="delete-workspace-title"
          >
            Delete “{workspace.title}”?
          </h2>
          <p className="mt-2 mb-0 text-[13px] leading-[1.62] text-text-secondary">
            This removes the workspace and its version history from Research Tree. The papers it
            points at are unaffected.
          </p>
          {error ? (
            <p
              className="mt-3.5 mb-0 rounded-lg border border-error-border bg-error-surface px-3.5 py-3 text-xs leading-[1.5] text-error"
              role="alert"
            >
              {error}
            </p>
          ) : null}
        </div>
        <div className="flex items-center gap-2 border-t border-hairline bg-surface-muted px-6 py-3.5">
          <button
            className={`${secondaryActionClass} ml-auto`}
            type="button"
            onClick={dismiss}
            disabled={busy}
          >
            Cancel
          </button>
          <button className={dangerActionClass} type="button" onClick={() => void remove()} disabled={busy}>
            {busy ? "Deleting…" : "Delete workspace"}
          </button>
        </div>
      </section>
    </div>
  );
}
