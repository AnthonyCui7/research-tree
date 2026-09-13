import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

type ModalDialog = {
  ref: RefObject<HTMLDialogElement | null>;
  /** Whether the dialog is playing its exit. */
  closing: boolean;
  /** Starts the exit; the element closes once it has finished. */
  dismiss: () => void;
};

/**
 * A modal `<dialog>` its parent mounts conditionally.
 *
 * `showModal()` is what makes the dialog modal: focus stays inside it, the page
 * behind is inert, and it sits in the top layer above anything with a z-index.
 * The element is opened the moment it exists, and `initialFocus` names the
 * control to start on when the first focusable one is not the right one.
 *
 * Leaving runs through the element. `dismiss` plays the exit and then calls
 * `close()`, and the element's `close` event, which the caller wires to its
 * `onClose`, is what has the parent unmount it. Closing rather than only
 * unmounting is what hands focus back to the control that opened the dialog,
 * and a close the browser forces on its own takes the same path, so the
 * parent's state can never say open while the screen says otherwise.
 *
 * Escape is answered by the caller in `keydown` with `preventDefault`, not by
 * the `cancel` event: browsers only let a page cancel that event while it
 * holds user activation, and a second Escape spends it. `cancel` still covers
 * close requests that are not keys.
 */
export function useModalDialog(
  durationMs: number,
  initialFocus?: RefObject<HTMLElement | null>,
): ModalDialog {
  const ref = useRef<HTMLDialogElement>(null);
  const [closing, setClosing] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) {
      dialog.showModal();
      initialFocus?.current?.focus();
    }
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, [initialFocus]);

  // A second dismissal during the exit is ignored rather than restarting it,
  // so a click on the backdrop followed by Escape still closes once.
  const dismiss = useCallback(() => {
    if (timerRef.current !== null) {
      return;
    }
    setClosing(true);
    timerRef.current = window.setTimeout(() => ref.current?.close(), durationMs);
  }, [durationMs]);

  return { ref, closing, dismiss };
}
