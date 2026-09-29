import { useCallback, useEffect, useRef, useState } from "react";

/**
 * How long each family of interface takes to leave. The values pair with the
 * `animate-*-exit` utilities in `app.css`: a mismatch either clips the animation
 * or leaves an invisible surface mounted, so they are declared once and shared.
 */
export const DROPDOWN_EXIT_MS = 110;
export const PANEL_EXIT_MS = 150;
export const DIALOG_EXIT_MS = 160;
export const TOAST_EXIT_MS = 150;

type ExitAnimation = {
  /** Whether the interface should be rendered at all. */
  present: boolean;
  /** Whether it is currently playing its exit animation. */
  closing: boolean;
};

/**
 * Keeps an interface mounted for the length of its exit animation.
 *
 * For interfaces that own their own open state — dropdowns, menus, the docked
 * panel. `open` is the intent; `present` is what should be rendered.
 */
export function useExitAnimation(open: boolean, durationMs: number): ExitAnimation {
  const [present, setPresent] = useState(open);

  useEffect(() => {
    if (open) {
      setPresent(true);
      return;
    }
    if (!present) {
      return;
    }
    const timer = window.setTimeout(() => setPresent(false), durationMs);
    return () => window.clearTimeout(timer);
  }, [durationMs, open, present]);

  // Present from the render that opens it, not the one after: the canvas
  // checks whether the selected card is still in view as that render lands,
  // and it has to see the width the panel leaves it.
  return { present: present || open, closing: present && !open };
}

type DismissAnimation = {
  closing: boolean;
  /** Starts the exit; the parent's `onClose` runs once it has finished. */
  dismiss: () => void;
};

/**
 * The mirror of `useExitAnimation` for an interface its parent mounts
 * conditionally — an overlay or a dialog. Reporting the close late is what gives
 * the exit animation somewhere to play: every dismissal inside the component
 * goes through `dismiss` rather than calling `onClose` directly.
 */
export function useDismissAnimation(onClose: () => void, durationMs: number): DismissAnimation {
  const [closing, setClosing] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  // A second dismissal during the exit is ignored rather than restarting it,
  // so a click on the backdrop followed by Escape still closes once.
  const dismiss = useCallback(() => {
    if (timerRef.current !== null) {
      return;
    }
    setClosing(true);
    timerRef.current = window.setTimeout(onClose, durationMs);
  }, [durationMs, onClose]);

  return { closing, dismiss };
}
