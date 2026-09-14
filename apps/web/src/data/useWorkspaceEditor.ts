import { useCallback, useEffect, useRef, useState } from "react";
import { repositoryWorkspaceGateway } from "./workspaceApi";
import { isVersionConflict, messageFrom, VERSION_CONFLICT_MESSAGE } from "../lib/apiError";
import type { WorkspaceEditOperation, WorkspaceEditResult } from "../lib/types";

export type EditNotice = {
  tone: "success" | "error";
  text: string;
  /** Restores the version the edit replaced. */
  undo?: () => void;
};

export type WorkspaceEditor = {
  busy: boolean;
  notice: EditNotice | null;
  dismissNotice: () => void;
  /** Applies the operations as one version; null when nothing was sent. */
  apply: (operations: WorkspaceEditOperation[]) => Promise<WorkspaceEditResult | null>;
};

/** A successful edit's toast leaves on its own; an error waits to be read. */
const NOTICE_MS = 8_000;

/**
 * Edits made by hand on the canvas: rename a branch, move or remove a paper.
 *
 * Every edit is sent against the version on screen, so one that lost a race
 * with another change is refused and the workspace reloads instead of
 * overwriting. The toast for a landed edit carries an undo, which is a restore
 * of the version the edit replaced, guarded by the version the edit made.
 */
export function useWorkspaceEditor(
  workspaceId: string | null,
  currentVersionHash: string | null,
  onChanged: () => Promise<void>,
): WorkspaceEditor {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<EditNotice | null>(null);
  const timerRef = useRef<number | null>(null);
  // The state is what the controls read; the ref is what refuses a second
  // edit that arrives before the first has rendered them disabled.
  const busyRef = useRef(false);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const show = useCallback(
    (next: EditNotice | null) => {
      clearTimer();
      setNotice(next);
      if (next?.tone === "success") {
        timerRef.current = window.setTimeout(() => setNotice(null), NOTICE_MS);
      }
    },
    [clearTimer],
  );

  useEffect(() => clearTimer, [clearTimer]);

  // A notice belongs to the workspace it was made in.
  useEffect(() => {
    show(null);
  }, [show, workspaceId]);

  const undo = useCallback(
    async (result: WorkspaceEditResult) => {
      if (!workspaceId || busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      try {
        await repositoryWorkspaceGateway.restoreWorkspace(
          workspaceId,
          result.previous_version_hash,
          result.workspace_version_hash,
        );
        show(null);
        await onChanged();
      } catch (error) {
        const conflict = isVersionConflict(error);
        show({ tone: "error", text: conflict ? VERSION_CONFLICT_MESSAGE : messageFrom(error) });
        if (conflict) await onChanged();
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [onChanged, show, workspaceId],
  );

  const apply = useCallback(
    async (operations: WorkspaceEditOperation[]) => {
      if (!workspaceId || !currentVersionHash || operations.length === 0) return null;
      if (busyRef.current) return null;
      busyRef.current = true;
      setBusy(true);
      try {
        const result = await repositoryWorkspaceGateway.editWorkspace(
          workspaceId,
          operations,
          currentVersionHash,
        );
        await onChanged();
        if (result.changed) {
          show({ tone: "success", text: result.summary, undo: () => void undo(result) });
        }
        return result;
      } catch (error) {
        const conflict = isVersionConflict(error);
        show({ tone: "error", text: conflict ? VERSION_CONFLICT_MESSAGE : messageFrom(error) });
        if (conflict) await onChanged();
        return null;
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [currentVersionHash, onChanged, show, undo, workspaceId],
  );

  return { busy, notice, dismissNotice: () => show(null), apply };
}
