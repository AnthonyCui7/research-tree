import type { ReactNode } from "react";
import { cx } from "../../lib/cx";
import { TOAST_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { CheckIcon, CloseIcon, WarningIcon } from "./icons";

type ToastProps = {
  tone: "success" | "error";
  children: ReactNode;
  onDismiss?: () => void;
  dismissLabel?: string;
};

const NO_DISMISS = () => {};

/**
 * The design's toast: a single line of consequence, tinted by outcome, resting
 * over the canvas rather than displacing it.
 */
export function Toast({ tone, children, onDismiss, dismissLabel = "Dismiss" }: ToastProps) {
  const success = tone === "success";
  const { closing, dismiss } = useDismissAnimation(onDismiss ?? NO_DISMISS, TOAST_EXIT_MS);
  return (
    <div
      className={cx(
        "flex items-start gap-2 rounded-lg border px-[13px] py-[9px] text-xs leading-[1.5] shadow-toast",
        closing ? "animate-toast-exit" : "animate-toast-enter",
        success
          ? "border-accent-border bg-accent-subtle text-accent-deep"
          : "border-error-border bg-surface text-error",
      )}
      role={success ? "status" : "alert"}
    >
      <span className="mt-[3px] flex-none">
        {success ? <CheckIcon className="h-3 w-3" /> : <WarningIcon className="h-3.5 w-3.5" />}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
      {onDismiss ? (
        <button
          className={cx(
            "-mr-1 grid h-5 w-5 flex-none place-items-center rounded-[5px] border-0 bg-transparent p-0 transition-[background-color] duration-150",
            success ? "hover:bg-accent-border" : "hover:bg-error-surface",
          )}
          type="button"
          onClick={dismiss}
          aria-label={dismissLabel}
          title={dismissLabel}
        >
          <CloseIcon className="h-2.5 w-2.5" />
        </button>
      ) : null}
    </div>
  );
}

/** Bottom-left stack the toasts rise from — the zoom stepper owns the right. */
export function ToastStack({ children }: { children: ReactNode }) {
  return (
    <div className="pointer-events-none absolute bottom-10 left-10 z-dropdown flex w-[min(400px,calc(100%-80px))] flex-col gap-2 max-[720px]:bottom-6 max-[720px]:left-6">
      <div className="pointer-events-auto flex w-full flex-col gap-2">{children}</div>
    </div>
  );
}
