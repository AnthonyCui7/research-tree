import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { cx } from "../../lib/cx";
import { DIALOG_EXIT_MS, useDismissAnimation } from "../../lib/animation";
import { primaryActionClass, textInputClass } from "../../lib/controlClasses";
import { CloseIcon } from "../ui/icons";

export type AuthMode = "sign-in" | "create-account";

type AuthScreensProps = {
  initialMode: AuthMode;
  onClose: () => void;
};

/**
 * The sign-in and create-account screens, visual-only for now: this build has
 * no account database and no OAuth, so every action lands on the same honest
 * notice instead of pretending to authenticate.
 */
export function AuthScreens({ initialMode, onClose }: AuthScreensProps) {
  const { closing, dismiss } = useDismissAnimation(onClose, DIALOG_EXIT_MS);
  const [mode, setMode] = useState<AuthMode>(initialMode);
  const [notice, setNotice] = useState(false);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      dismiss();
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [dismiss]);

  function unavailable(event?: FormEvent) {
    event?.preventDefault();
    setNotice(true);
  }

  return (
    <div
      className={cx(
        "fixed inset-0 z-creator flex items-center justify-center bg-[#f7f8f8] bg-[radial-gradient(#dcdfe2_1px,transparent_1px)] bg-[length:22px_22px]",
        closing ? "animate-backdrop-exit" : "animate-backdrop-enter",
      )}
      role="dialog"
      aria-modal="true"
      aria-label={mode === "sign-in" ? "Sign in" : "Create account"}
    >
      <button
        className="absolute top-4 right-4 grid h-8 w-8 place-items-center rounded-[8px] border border-border bg-surface p-0 text-text-muted transition-[background-color,color] duration-150 hover:bg-surface-subtle hover:text-text-primary"
        type="button"
        onClick={dismiss}
        aria-label="Close"
        title="Close"
      >
        <CloseIcon className="h-3 w-3" />
      </button>

      <form
        className="w-[360px] rounded-[14px] border border-border bg-surface px-8 pt-8 pb-7 shadow-dialog"
        onSubmit={unavailable}
      >
        <div className="text-sm font-bold tracking-[-0.01em] text-text-primary">Research Tree</div>
        <h2 className="mt-[22px] mb-0 text-[19px] font-bold tracking-[-0.015em] text-text-primary">
          {mode === "sign-in" ? "Sign in" : "Create account"}
        </h2>

        <button
          className="mt-[18px] flex w-full items-center justify-center gap-2.5 rounded-[9px] border border-border-strong bg-surface px-3.5 py-[9px] text-[13px] font-semibold text-text-primary transition-[background-color] duration-150 hover:bg-surface-subtle"
          type="button"
          onClick={() => unavailable()}
        >
          <GoogleMark />
          Continue with Google
        </button>

        <div className="my-[18px] flex items-center gap-2.5" role="presentation">
          <span className="h-px flex-1 bg-hairline" />
          <span className="text-[11px] text-text-muted">or</span>
          <span className="h-px flex-1 bg-hairline" />
        </div>

        <Field label="Email">
          <input
            className={textInputClass}
            type="email"
            name="email"
            placeholder="you@university.edu"
            autoComplete="off"
          />
        </Field>
        {mode === "create-account" ? (
          <div className="mt-3.5">
            <Field label="Password">
              <input
                className={textInputClass}
                type="password"
                name="password"
                placeholder="••••••••••"
                autoComplete="new-password"
              />
            </Field>
            <p className="mt-1.5 mb-0 text-[11px] text-text-muted">At least 8 characters</p>
          </div>
        ) : null}

        <button className={cx(primaryActionClass, "mt-3.5 w-full")} type="submit">
          {mode === "sign-in" ? "Continue" : "Create account"}
        </button>

        {notice ? (
          <p
            className="mt-3.5 mb-0 rounded-[8px] border border-border bg-surface-subtle px-3 py-2 text-[11.5px] leading-[1.5] text-text-secondary"
            role="status"
          >
            Accounts aren&rsquo;t connected yet — this build of Research Tree runs locally on your
            device.
          </p>
        ) : null}

        <div className="mt-5 border-t border-hairline pt-4 text-xs text-text-secondary">
          {mode === "sign-in" ? (
            <>
              New here?{" "}
              <ToggleLink onClick={() => setMode("create-account")}>Create an account</ToggleLink>
            </>
          ) : (
            <>
              Have an account? <ToggleLink onClick={() => setMode("sign-in")}>Sign in</ToggleLink>
            </>
          )}
        </div>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-text-primary">{label}</span>
      {children}
    </label>
  );
}

function ToggleLink({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button
      className="border-0 bg-transparent p-0 text-xs font-semibold text-accent transition-[color] duration-150 hover:text-accent-deep hover:underline"
      type="button"
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.57 2.68-3.88 2.68-6.62z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.59A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
      />
    </svg>
  );
}
