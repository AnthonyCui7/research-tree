import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { TOAST_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { inlineIconButtonClass } from "../../lib/controlClasses";
import { CheckIcon, CloseIcon, WarningIcon } from "./icons";

type ToastProps = {
  tone: "success" | "error";
  children: ReactNode;
  onDismiss?: () => void;
  dismissLabel?: string;
  /** One follow-up the toast offers, such as undoing what it reports. */
  action?: { label: string; onClick: () => void; disabled?: boolean };
};

const NO_DISMISS = () => {};

/**
 * A single line of consequence, resting over the canvas rather than
 * displacing it. The icon carries the outcome; the card stays neutral. The
 * right padding is the left's less the 6px inside the dismiss button, so the
 * two glyphs sit the same 16px from their edges.
 */
export function Toast({ tone, children, onDismiss, dismissLabel = "Dismiss", action }: ToastProps) {
  const success = tone === "success";
  const { closing, dismiss } = useDismissAnimation(onDismiss ?? NO_DISMISS, TOAST_EXIT_MS);
  return (
    <div
      className={cx(
        "flex items-start gap-2.5 rounded-xl border border-border bg-surface py-3 pr-2.5 pl-4 text-13 text-text-primary shadow-toast",
        closing ? "animate-toast-exit" : "animate-toast-enter",
      )}
      role={success ? "status" : "alert"}
    >
      <span className={cx("mt-0.5 flex-none", success ? "text-accent" : "text-error")}>
        {success ? <CheckIcon className="size-4" /> : <WarningIcon className="size-4" />}
      </span>
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{children}</span>
      {action ? (
        <button
          className="-my-0.5 h-6 flex-none rounded-sm border-0 bg-transparent px-2 text-13 font-semibold text-accent-deep transition-[background-color] duration-150 enabled:hover:bg-accent-subtle disabled:cursor-not-allowed disabled:text-text-muted"
          type="button"
          onClick={action.onClick}
          disabled={action.disabled}
        >
          {action.label}
        </button>
      ) : null}
      {onDismiss ? (
        <button
          className={cx(inlineIconButtonClass, "text-text-muted hover:bg-surface-subtle hover:text-text-primary")}
          type="button"
          onClick={dismiss}
          aria-label={dismissLabel}
          title={dismissLabel}
        >
          <CloseIcon className="size-3" />
        </button>
      ) : null}
    </div>
  );
}

/**
 * Bottom-left stack the toasts rise from — the zoom stepper owns the right —
 * 16px in from the corner, as the stepper is from its own, so the two share a
 * bottom edge.
 */
export function ToastStack({ children }: { children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute bottom-4 left-4 z-dropdown flex w-[min(420px,calc(100%-32px))] flex-col gap-2">
      <div className="pointer-events-auto flex w-full flex-col gap-2">{children}</div>
    </div>
  );
}
